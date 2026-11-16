from __future__ import annotations

import sqlite3
from typing import Any


def next_batch_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM apply_batches").fetchone()["c"] + 1
    return f"AB-{n:04d}"


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    clock_at: str,
    ticket_count: int,
    line_count: int,
    status: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batches(batch_no, clock_at, ticket_count, line_count, status, created_at)
        VALUES (?,?,?,?,?,?)
        ''',
        (batch_no, clock_at, ticket_count, line_count, status, created_at),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def has_running(conn: sqlite3.Connection) -> bool:
    """是否存在应用到期进行中的批次（进行中禁止改钟）。"""
    row = conn.execute(
        "SELECT COUNT(*) c FROM apply_batches WHERE status='running'"
    ).fetchone()
    return int(row["c"]) > 0


def mark_superseded(
    conn: sqlite3.Connection,
    batch_ids: list[int],
    *,
    note: str,
) -> None:
    """回拨后，被波及的已完成批次标记为 superseded 并追加说明（批次记录保留留痕）。"""
    if not batch_ids:
        return
    placeholders = ",".join("?" for _ in batch_ids)
    conn.execute(
        f"UPDATE apply_batches SET status='superseded', note=? WHERE id IN ({placeholders})",
        (note, *batch_ids),
    )
