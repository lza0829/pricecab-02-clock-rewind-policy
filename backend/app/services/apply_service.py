from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo
from app.services import clock_service


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    return clock_service.get_clock()


def set_clock(clock_at: str) -> dict[str, Any]:
    # 回拨策略、应用进行中守卫、留痕全部收敛在 clock_service。
    return clock_service.set_clock(clock_at)


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def apply_due() -> dict[str, Any]:
    """Apply all scheduled tickets whose effective_at <= business clock."""
    conn = connect(immediate=True)
    try:
        # 双保险：已有进行中批次时拒绝再次应用（该批次也同时阻塞改钟）。
        if batch_repo.has_running(conn):
            raise HTTPException(409, "已有应用到期批次进行中，请等待其完成")
        clock = settings_repo.get(conn, "business_clock")
        if not clock:
            raise HTTPException(400, "业务钟未设置")
        due = ticket_repo.due_tickets(conn, clock)
        if not due:
            conn.commit()
            return {
                "applied": False,
                "message": "当前业务钟下没有到期改价单",
                "business_clock": clock,
                "ticket_count": 0,
                "line_count": 0,
            }

        batch_no = batch_repo.next_batch_no(conn)
        created = _now()
        line_total = 0
        applied_tickets = []
        # create batch first with running status;改钟接口见到 running 会拒绝。
        batch_id = batch_repo.create_batch(
            conn,
            batch_no=batch_no,
            clock_at=clock,
            ticket_count=0,
            line_count=0,
            status="running",
            created_at=created,
        )

        for t in due:
            lines = ticket_repo.list_lines(conn, t["id"])
            for line in lines:
                price_repo.apply_price(
                    conn,
                    sku_code=line["sku_code"],
                    price=float(line["new_price"]),
                    ticket_no=t["ticket_no"],
                    effective_at=t["effective_at"],
                    updated_at=created,
                )
                line_total += 1
            ticket_repo.mark_applied(conn, t["id"], applied_at=created, batch_id=batch_id)
            applied_tickets.append(t["ticket_no"])

        conn.execute(
            "UPDATE apply_batches SET ticket_count=?, line_count=?, status=? WHERE id=?",
            (len(due), line_total, "done", batch_id),
        )
        conn.commit()
        return {
            "applied": True,
            "batch_no": batch_no,
            "business_clock": clock,
            "ticket_count": len(due),
            "line_count": line_total,
            "tickets": applied_tickets,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
