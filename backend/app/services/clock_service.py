"""业务钟服务：读取/拨动业务钟，并按全局唯一的回拨策略处理回拨。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import batches as batch_repo
from app.repositories import clock_events as event_repo
from app.repositories import prices as price_repo
from app.repositories import settings as settings_repo
from app.repositories import skus as sku_repo
from app.repositories import tickets as ticket_repo
from app.services import clock_policy as policy


CLOCK_KEY = "business_clock"
POLICY_KEY = "clock_rewind_policy"
POLICY_LABEL_KEY = "clock_rewind_policy_label"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """仅接受明确的 ISO-8601 时刻，统一按 UTC 比较，避免字符串字典序歧义。"""
    text = (value or "").strip()
    if not text:
        raise HTTPException(400, "业务钟不能为空")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        raise HTTPException(400, f"业务钟时刻格式非法：{text!r}，应为 ISO-8601，如 2026-11-16T09:00:00Z")
    if dt.tzinfo is None:
        raise HTTPException(400, "业务钟时刻必须带时区（建议 UTC，Z 结尾）")
    return dt.astimezone(timezone.utc)


def _canonical(value: str) -> str:
    return _parse_iso(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_policy(conn) -> str:
    return policy.normalize(settings_repo.get(conn, POLICY_KEY, policy.DEFAULT_POLICY))


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        current = settings_repo.get(conn, CLOCK_KEY)
        active_policy = get_policy(conn)
        return {
            "business_clock": current,
            "clock_label": settings_repo.get(conn, "clock_label"),
            "clock_rewind_policy": active_policy,
            "clock_rewind_policy_label": policy.POLICY_LABELS[active_policy],
            "available_policies": list(policy.POLICIES),
        }
    finally:
        conn.close()


def set_policy(new_policy: str) -> dict[str, Any]:
    """配置全局唯一的回拨策略（两种互斥，任一时刻只生效一种）。"""
    value = policy.normalize(new_policy)
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, POLICY_KEY, value)
        settings_repo.set_value(conn, POLICY_LABEL_KEY, policy.POLICY_LABELS[value])
        conn.commit()
        return get_clock()
    finally:
        conn.close()


def list_events(limit: int = 100) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return event_repo.list_events(conn, limit=limit)
    finally:
        conn.close()


def _rebuild_price_book(conn, *, new_clock: str, at: str) -> dict[str, Any]:
    """按价格历史把价签本恢复到新钟点应有的现行价。

    每个 SKU 取未作废且 effective_at <= 新钟点的最新历史；没有则回落目录挂牌价。
    重建后价签本不得残留以被作废应用为来源的现行价。
    """
    restored = 0
    fallback = 0
    restore_note = f"业务钟回拨至 {new_clock} 后按价格历史恢复的现行价"
    for sku in sku_repo.list_skus(conn):
        code = sku["sku_code"]
        latest = price_repo.latest_active(conn, code)
        if latest is not None and latest["effective_at"] <= new_clock:
            price_repo.upsert_book(
                conn,
                sku_code=code,
                price=float(latest["price"]),
                ticket_no=latest["source_ticket_no"],
                effective_at=latest["effective_at"],
                updated_at=at,
            )
            # 对充当恢复基准的那条历史追加恢复说明（不改价、不删行）。
            if latest.get("note") != restore_note:
                price_repo.set_history_note(conn, history_id=latest["id"], note=restore_note)
            restored += 1
        else:
            # 新钟点之前没有任何有效历史：回落目录挂牌价，来源不指向任何应用。
            price_repo.upsert_book(
                conn,
                sku_code=code,
                price=float(sku["list_price"]),
                ticket_no=None,
                effective_at=None,
                updated_at=at,
            )
            fallback += 1
    return {"restored_skus": restored, "fallback_list_price_skus": fallback}


def set_clock(clock_at: str) -> dict[str, Any]:
    new_clock = _canonical(clock_at)
    conn = connect(immediate=True)
    try:
        # 应用到期进行中禁止改钟（running 批次由应用事务持有，双保险）。
        if batch_repo.has_running(conn):
            raise HTTPException(409, "应用到期进行中，禁止修改业务钟")

        current = settings_repo.get(conn, CLOCK_KEY)
        active_policy = get_policy(conn)
        at = _now()

        # 首次设钟或前拨：直接推进，不动已应用批次与价签本。
        if current is None:
            settings_repo.set_value(conn, CLOCK_KEY, new_clock)
            event_repo.record(
                conn,
                event_type="clock_init",
                from_clock=None,
                to_clock=new_clock,
                policy=active_policy,
                detail={"message": "首次设置业务钟"},
                created_at=at,
            )
            conn.commit()
            return get_clock()

        current_dt = _parse_iso(current)
        new_dt = _parse_iso(new_clock)

        if new_dt >= current_dt:
            settings_repo.set_value(conn, CLOCK_KEY, new_clock)
            event_repo.record(
                conn,
                event_type="clock_forward",
                from_clock=current,
                to_clock=new_clock,
                policy=active_policy,
                detail={"message": "业务钟前拨/持平，已应用结果保持不动"},
                created_at=at,
            )
            conn.commit()
            return get_clock()

        # 新钟点早于当前业务钟 => 回拨，按全局唯一策略处理。
        if active_policy == policy.FORBID:
            # 禁止回拨：拒绝，且已应用批次与价签本不动（不做任何写操作）。
            raise HTTPException(
                409,
                f"当前策略禁止回拨：新钟点 {new_clock} 早于当前业务钟 {current}，"
                "已应用批次与价签本保持不变",
            )

        # 允许回拨（allow_rewind_reschedule）：作废回拨点之后的生效结果。
        affected = ticket_repo.applied_tickets_after(conn, new_clock)
        affected_nos = [t["ticket_no"] for t in affected]
        batch_ids = sorted({int(t["apply_batch_id"]) for t in affected if t["apply_batch_id"] is not None})

        void_note = f"业务钟回拨至 {new_clock}：生效时刻晚于新钟点的应用结果作废"
        voided_rows = price_repo.void_history_after(conn, new_clock=new_clock, note=void_note)

        # 被作废改价单固定改回「可再调度」态（scheduled），而非作废态。
        for t in affected:
            ticket_repo.reschedule_ticket(conn, t["id"])

        superseded_note = f"业务钟回拨至 {new_clock}：批次内回拨点之后的改价单已作废并回到可再调度态"
        batch_repo.mark_superseded(conn, batch_ids, note=superseded_note)

        # 按价格历史把价签本恢复到新钟点应有现行价（清除被作废来源的现行价）。
        rebuild = _rebuild_price_book(conn, new_clock=new_clock, at=at)

        settings_repo.set_value(conn, CLOCK_KEY, new_clock)
        event_repo.record(
            conn,
            event_type="clock_rewind",
            from_clock=current,
            to_clock=new_clock,
            policy=active_policy,
            detail={
                "message": "业务钟回拨：回拨点之后的生效结果已作废，改价单回到可再调度态，价签本按历史恢复",
                "rescheduled_tickets": affected_nos,
                "superseded_batch_ids": batch_ids,
                "voided_history_rows": voided_rows,
                **rebuild,
            },
            created_at=at,
        )
        conn.commit()
        result = get_clock()
        result["rewind"] = {
            "rescheduled_tickets": affected_nos,
            "superseded_batch_ids": batch_ids,
            "voided_history_rows": voided_rows,
            **rebuild,
        }
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
