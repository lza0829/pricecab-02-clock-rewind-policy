"""回拨策略端到端测试（FastAPI TestClient + 临时 SQLite）。

覆盖题目硬性要求：
1. 禁止策略下回拨被拒，且已应用批次与价签本不动；
2. 允许策略下回拨后价签与对账一致（历史链不残留被作废来源的现行价）；
3. 应用到期进行中改钟被拒；
并保证两种策略互斥、价格历史只追加不删除。
"""

from __future__ import annotations


def _create_ticket(client, *, title, effective_at, lines):
    r = client.post(
        "/api/tickets",
        json={"title": title, "effective_at": effective_at, "lines": lines},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _book_map(client):
    return {r["sku_code"]: r for r in client.get("/api/prices/book").json()}


def _history(client, sku):
    return [h for h in client.get("/api/prices/history").json() if h["sku_code"] == sku]


def _set_policy(client, policy_value):
    r = client.put("/api/settings/clock-rewind-policy", json={"clock_rewind_policy": policy_value})
    assert r.status_code == 200, r.text
    assert r.json()["clock_rewind_policy"] == policy_value


def test_forbid_policy_rejects_rewind_and_keeps_book_unchanged(client):
    # 默认策略即 forbid_rewind。
    clock = client.get("/api/apply/clock").json()
    assert clock["clock_rewind_policy"] == "forbid_rewind"

    # 建一张晚于初始钟点的改价单并应用。
    ticket = _create_ticket(
        client,
        title="茶饮提价",
        effective_at="2026-11-16T10:00:00Z",
        lines=[{"sku_code": "SKU-TEA-001", "new_price": 33.0}],
    )
    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T11:00:00Z"})
    applied = client.post("/api/apply/due").json()
    assert applied["applied"] is True

    before_book = _book_map(client)["SKU-TEA-001"]
    assert before_book["current_price"] == 33.0
    before_history_count = len(_history(client, "SKU-TEA-001"))

    # 回拨到应用点之前：禁止策略必须拒绝（409）。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T09:30:00Z"})
    assert r.status_code == 409, r.text
    assert "禁止回拨" in r.json()["detail"]

    # 钟点未变、价签本未变、批次未变、历史未被删除。
    assert client.get("/api/apply/clock").json()["business_clock"] == "2026-11-16T11:00:00Z"
    after_book = _book_map(client)["SKU-TEA-001"]
    assert after_book["current_price"] == 33.0
    assert after_book["source_ticket_no"] == ticket["ticket_no"]
    assert len(_history(client, "SKU-TEA-001")) == before_history_count
    tickets = {t["ticket_no"]: t for t in client.get("/api/tickets").json()}
    assert tickets[ticket["ticket_no"]]["status"] == "applied"
    batches = client.get("/api/apply/batches").json()
    assert all(b["status"] == "done" for b in batches)

    # 前拨仍然放行。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T12:00:00Z"})
    assert r.status_code == 200
    assert r.json()["business_clock"] == "2026-11-16T12:00:00Z"


def test_allow_policy_rewind_restores_book_and_reconciles(client):
    _set_policy(client, "allow_rewind_reschedule")

    # 两张改价单：T1 在 10:00 生效（28 -> 30），T2 在 12:00 生效（30 -> 35）。
    t1 = _create_ticket(
        client,
        title="上午调价",
        effective_at="2026-11-16T10:00:00Z",
        lines=[{"sku_code": "SKU-TEA-001", "new_price": 30.0}],
    )
    t2 = _create_ticket(
        client,
        title="中午调价",
        effective_at="2026-11-16T12:00:00Z",
        lines=[{"sku_code": "SKU-TEA-001", "new_price": 35.0}],
    )

    # 钟拨到 13:00，两张都到期并应用，价签现为 35（来源 T2）。
    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T13:00:00Z"})
    client.post("/api/apply/due")
    book = _book_map(client)["SKU-TEA-001"]
    assert book["current_price"] == 35.0
    assert book["source_ticket_no"] == t2["ticket_no"]

    history_before = _history(client, "SKU-TEA-001")
    assert [h["price"] for h in history_before][::-1] == [28.0, 30.0, 35.0]

    # 回拨到 11:00：T2（12:00）应作废并回到可再调度；T1（10:00）仍有效。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T11:00:00Z"})
    assert r.status_code == 200, r.text
    rewind = r.json()["rewind"]
    assert rewind["rescheduled_tickets"] == [t2["ticket_no"]]
    assert rewind["voided_history_rows"] == 1

    # 价签本恢复为新钟点应有现行价 30（来源 T1），不得残留 T2 的 35。
    book = _book_map(client)["SKU-TEA-001"]
    assert book["current_price"] == 30.0
    assert book["source_ticket_no"] == t1["ticket_no"]

    # 价格历史未被静默删除：仍是 3 行，仅 T2 那行被标注 voided + 作废说明。
    history_after = _history(client, "SKU-TEA-001")
    assert len(history_after) == 3
    by_price = {h["price"]: h for h in history_after}
    assert by_price[35.0]["status"] == "voided"
    assert by_price[35.0]["source_ticket_no"] == t2["ticket_no"]
    assert "作废" in (by_price[35.0]["note"] or "")
    assert by_price[30.0]["status"] == "active"
    assert by_price[28.0]["status"] == "active"

    # T2 回到 scheduled（可再调度），T1 保持 applied；批次被标记 superseded。
    tickets = {t["ticket_no"]: t for t in client.get("/api/tickets").json()}
    assert tickets[t2["ticket_no"]]["status"] == "scheduled"
    assert tickets[t2["ticket_no"]]["applied_at"] is None
    assert tickets[t1["ticket_no"]]["status"] == "applied"
    batches = client.get("/api/apply/batches").json()
    assert any(b["status"] == "superseded" for b in batches)

    # 对账按 sold_at 回溯，只认未作废历史，与恢复后的历史链一致。
    recon = client.get("/api/reconcile/sales").json()
    tea_sales = [row for row in recon["rows"] if row["sku_code"] == "SKU-TEA-001"]
    # S-1001 成交于 08:10、单价 28：当时有效价为初始 28 => match
    s1001 = next(row for row in tea_sales if row["sale_no"] == "S-1001")
    assert s1001["expected_price"] == 28.0
    assert s1001["status"] == "match"

    # 回拨留痕：存在 clock_rewind 事件且包含被作废单据。
    events = client.get("/api/apply/clock/events").json()["events"]
    rewind_events = [e for e in events if e["event_type"] == "clock_rewind"]
    assert len(rewind_events) == 1
    assert t2["ticket_no"] in rewind_events[0]["detail"]["rescheduled_tickets"]

    # 再前拨到 13:00 并重新应用：T2 可再次被调度应用，价签重新变为 35，历史追加而非覆盖。
    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T13:00:00Z"})
    client.post("/api/apply/due")
    book = _book_map(client)["SKU-TEA-001"]
    assert book["current_price"] == 35.0
    history_final = _history(client, "SKU-TEA-001")
    assert len(history_final) == 4  # 原 3 行（含 voided）+ 重新应用追加 1 行
    newest = history_final[0]
    assert newest["price"] == 35.0 and newest["status"] == "active"


def test_rewind_before_any_change_falls_back_to_list_price(client):
    _set_policy(client, "allow_rewind_reschedule")
    # 先应用一张 10:00 的改价，再回拨到 09:00（早于该 SKU 一切改价，初始历史 effective_at=seed 时刻）。
    _create_ticket(
        client,
        title="零食调价",
        effective_at="2026-11-16T10:00:00Z",
        lines=[{"sku_code": "SKU-SNK-012", "new_price": 49.9}],
    )
    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T11:00:00Z"})
    client.post("/api/apply/due")
    assert _book_map(client)["SKU-SNK-012"]["current_price"] == 49.9

    # 回拨到早于初始历史 effective_at 的钟点：无有效历史，回落目录挂牌价 39.9。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T08:00:00Z"})
    assert r.status_code == 200
    book = _book_map(client)["SKU-SNK-012"]
    assert book["current_price"] == 39.9
    assert book["source_ticket_no"] is None


def test_clock_change_rejected_while_apply_running(client, monkeypatch):
    from app.repositories import batches as batch_repo
    from app.db import connect

    # 直接插入一个 running 批次模拟“应用到期进行中”。
    conn = connect(immediate=True)
    try:
        batch_repo.create_batch(
            conn,
            batch_no="AB-RUN-1",
            clock_at="2026-11-16T09:00:00Z",
            ticket_count=0,
            line_count=0,
            status="running",
            created_at="2026-11-16T09:00:00Z",
        )
        conn.commit()
    finally:
        conn.close()

    # 无论前拨还是回拨，进行中一律拒绝。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T12:00:00Z"})
    assert r.status_code == 409
    assert "应用到期进行中" in r.json()["detail"]

    _set_policy(client, "allow_rewind_reschedule")
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T08:00:00Z"})
    assert r.status_code == 409
    assert "应用到期进行中" in r.json()["detail"]

    # 同时 apply_due 自身也被守卫挡住。
    r = client.post("/api/apply/due")
    assert r.status_code == 409


def test_policies_are_mutually_exclusive_and_validated(client):
    # 同一时刻只能设置一种策略，非法取值被拒。
    r = client.put(
        "/api/settings/clock-rewind-policy",
        json={"clock_rewind_policy": "both_at_once"},
    )
    assert r.status_code == 400
    options = client.get("/api/settings/clock-rewind-policy/options").json()
    assert set(options["policies"]) == {"forbid_rewind", "allow_rewind_reschedule"}

    # 切换到允许策略后立即生效，且只有这一种。
    _set_policy(client, "allow_rewind_reschedule")
    assert client.get("/api/apply/clock").json()["clock_rewind_policy"] == "allow_rewind_reschedule"
