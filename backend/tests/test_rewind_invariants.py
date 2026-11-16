"""回拨后全局不变量与对账历史链一致性测试。"""

from __future__ import annotations


def _create(client, title, effective_at, lines):
    r = client.post(
        "/api/tickets",
        json={"title": title, "effective_at": effective_at, "lines": lines},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _set_policy(client, value):
    r = client.put("/api/settings/clock-rewind-policy", json={"clock_rewind_policy": value})
    assert r.status_code == 200


def test_no_book_row_references_voided_source_and_reconcile_uses_active_chain(client):
    from app.db import connect

    _set_policy(client, "allow_rewind_reschedule")

    # 两个 SKU 各有一张 10:00 与一张 12:00 的改价。
    t1a = _create(client, "A-上午", "2026-11-16T10:00:00Z",
                  [{"sku_code": "SKU-TEA-001", "new_price": 30.0}])
    t2a = _create(client, "A-中午", "2026-11-16T12:00:00Z",
                  [{"sku_code": "SKU-TEA-001", "new_price": 35.0}])
    t1b = _create(client, "B-上午", "2026-11-16T10:00:00Z",
                  [{"sku_code": "SKU-SNK-012", "new_price": 41.0}])
    t2b = _create(client, "B-中午", "2026-11-16T12:00:00Z",
                  [{"sku_code": "SKU-SNK-012", "new_price": 44.0}])

    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T13:00:00Z"})
    client.post("/api/apply/due")

    # 插入一笔成交于 12:30（落在将被作废的中午价时段）的销售。
    conn = connect(immediate=True)
    try:
        conn.execute(
            "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
            ("S-9001", "SKU-TEA-001", "2026-11-16T12:30:00Z", 35.0, 1),
        )
        conn.commit()
    finally:
        conn.close()

    # 回拨到 11:00：两张 12:00 改价（t2a/t2b）作废并回到可再调度。
    r = client.put("/api/apply/clock", json={"business_clock": "2026-11-16T11:00:00Z"})
    assert r.status_code == 200
    assert sorted(r.json()["rewind"]["rescheduled_tickets"]) == sorted(
        [t2a["ticket_no"], t2b["ticket_no"]]
    )

    # 不变量 1：价签本每一行的来源都不指向被作废（voided）历史。
    book = {b["sku_code"]: b for b in client.get("/api/prices/book").json()}
    history = client.get("/api/prices/history").json()
    voided_sources = {
        (h["sku_code"], h["source_ticket_no"])
        for h in history
        if h["status"] == "voided"
    }
    for code, row in book.items():
        if row["source_ticket_no"]:
            assert (code, row["source_ticket_no"]) not in voided_sources, (
                f"{code} 价签残留被作废来源 {row['source_ticket_no']}"
            )
    assert book["SKU-TEA-001"]["current_price"] == 30.0
    assert book["SKU-TEA-001"]["source_ticket_no"] == t1a["ticket_no"]
    assert book["SKU-SNK-012"]["current_price"] == 41.0
    assert book["SKU-SNK-012"]["source_ticket_no"] == t1b["ticket_no"]

    # 不变量 2：12:30 的销售对账取恢复后的有效链（10:00 的 30），而非被作废的 35。
    recon = client.get("/api/reconcile/sales").json()
    s9001 = next(x for x in recon["rows"] if x["sale_no"] == "S-9001")
    assert s9001["expected_price"] == 30.0
    assert s9001["status"] == "mismatch"  # 成交 35 vs 恢复链应价 30
    assert s9001["delta"] == 5.0

    # 历史链未被删除：被作废行仍在，仅状态变化。
    tea = [h for h in history if h["sku_code"] == "SKU-TEA-001"]
    assert len(tea) == 3  # 初始 28、上午 30、中午 35（voided）
    voided = [h for h in tea if h["status"] == "voided"]
    assert len(voided) == 1 and voided[0]["price"] == 35.0
