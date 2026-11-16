from __future__ import annotations

import json
import sqlite3
from typing import Any


def record(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    from_clock: str | None,
    to_clock: str,
    policy: str,
    detail: dict[str, Any],
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO clock_events(event_type, from_clock, to_clock, policy, detail, created_at)
        VALUES (?,?,?,?,?,?)
        ''',
        (event_type, from_clock, to_clock, policy, json.dumps(detail, ensure_ascii=False), created_at),
    )
    return int(cur.lastrowid)


def list_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM clock_events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["detail"] = json.loads(item["detail"])
        except (TypeError, ValueError):
            pass
        out.append(item)
    return out
