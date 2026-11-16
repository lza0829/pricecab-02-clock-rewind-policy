from __future__ import annotations

import sqlite3
from typing import Any

from app.services.clock_policy import HISTORY_ACTIVE


def list_book(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT b.*, s.title, s.category
        FROM price_book b
        JOIN skus s ON s.sku_code = b.sku_code
        ORDER BY b.sku_code
        '''
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_book(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str | None,
    effective_at: str | None,
    updated_at: str,
) -> None:
    """仅更新价签本，不追加历史（供回拨重建价签使用）。"""
    conn.execute(
        '''
        INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(sku_code) DO UPDATE SET
          current_price=excluded.current_price,
          source_ticket_no=excluded.source_ticket_no,
          effective_at=excluded.effective_at,
          updated_at=excluded.updated_at
        ''',
        (sku_code, price, ticket_no, effective_at, updated_at),
    )


def append_history(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str | None,
    effective_at: str,
    recorded_at: str,
    note: str | None = None,
) -> None:
    """价格历史只追加，不修改既有行的价格（回拨也只追加/标注，不删历史）。"""
    conn.execute(
        '''
        INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at, status, note)
        VALUES (?,?,?,?,?,?,?)
        ''',
        (sku_code, price, ticket_no, effective_at, recorded_at, HISTORY_ACTIVE, note),
    )


def apply_price(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str,
    effective_at: str,
    updated_at: str,
) -> None:
    """改价应用：价签本 upsert + 历史追加（历史保持只追加）。"""
    upsert_book(
        conn,
        sku_code=sku_code,
        price=price,
        ticket_no=ticket_no,
        effective_at=effective_at,
        updated_at=updated_at,
    )
    append_history(
        conn,
        sku_code=sku_code,
        price=price,
        ticket_no=ticket_no,
        effective_at=effective_at,
        recorded_at=updated_at,
    )


def list_history(conn: sqlite3.Connection, sku_code: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if sku_code:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE sku_code=? ORDER BY id DESC LIMIT ?",
            (sku_code, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM price_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def price_at(conn: sqlite3.Connection, sku_code: str, at: str) -> float | None:
    """某时刻的有效价：只取未作废（active）且 effective_at <= at 的最新一条。"""
    row = conn.execute(
        '''
        SELECT price FROM price_history
        WHERE sku_code=? AND effective_at <= ? AND status=?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, at, HISTORY_ACTIVE),
    ).fetchone()
    return float(row["price"]) if row else None


def latest_active(conn: sqlite3.Connection, sku_code: str) -> dict[str, Any] | None:
    row = conn.execute(
        '''
        SELECT * FROM price_history
        WHERE sku_code=? AND status=?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, HISTORY_ACTIVE),
    ).fetchone()
    return dict(row) if row else None


def void_history_after(
    conn: sqlite3.Connection,
    *,
    new_clock: str,
    note: str,
) -> int:
    """作废生效时刻晚于新钟点、且来自已应用改价单的历史行。

    价格历史不删除、不改价，只把 status 置为 voided 并追加作废说明。
    初始价（source_ticket_no IS NULL）不作废。
    """
    cur = conn.execute(
        '''
        UPDATE price_history
        SET status='voided', note=?
        WHERE effective_at > ?
          AND source_ticket_no IS NOT NULL
          AND status='active'
        ''',
        (note, new_clock),
    )
    return int(cur.rowcount or 0)


def set_history_note(conn: sqlite3.Connection, *, history_id: int, note: str) -> None:
    """对单条历史追加说明（不改价、不删行），用于标注回拨后的恢复基准。"""
    conn.execute(
        "UPDATE price_history SET note=? WHERE id=?",
        (note, history_id),
    )
