from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect
from app.services.clock_policy import (
    DEFAULT_POLICY,
    HISTORY_ACTIVE,
    POLICY_LABELS,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _table_columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_schema(conn) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skus (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          list_price REAL NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS price_tickets (
          id INTEGER PRIMARY KEY,
          ticket_no TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          effective_at TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          applied_at TEXT,
          apply_batch_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS price_ticket_lines (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER NOT NULL REFERENCES price_tickets(id) ON DELETE CASCADE,
          sku_code TEXT NOT NULL,
          old_price REAL NOT NULL,
          new_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batches (
          id INTEGER PRIMARY KEY,
          batch_no TEXT NOT NULL UNIQUE,
          clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS price_book (
          sku_code TEXT PRIMARY KEY,
          current_price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL,
          price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS sales_fixture (
          id INTEGER PRIMARY KEY,
          sale_no TEXT NOT NULL UNIQUE,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hold_applies (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clock_events (
          id INTEGER PRIMARY KEY,
          event_type TEXT NOT NULL,
          from_clock TEXT,
          to_clock TEXT NOT NULL,
          policy TEXT NOT NULL,
          detail TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        '''
    )
    # 轻量迁移：为旧库补齐回拨特性所需列，历史价格一律视为仍有效。
    if "status" not in _table_columns(conn, "price_history"):
        conn.execute("ALTER TABLE price_history ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "note" not in _table_columns(conn, "price_history"):
        conn.execute("ALTER TABLE price_history ADD COLUMN note TEXT")
    if "note" not in _table_columns(conn, "apply_batches"):
        conn.execute("ALTER TABLE apply_batches ADD COLUMN note TEXT")
    # 迁移期把缺省状态补齐，避免旧行 status 为空被对账漏算。
    conn.execute(
        f"UPDATE price_history SET status='{HISTORY_ACTIVE}' WHERE status IS NULL OR status=''"
    )


def seed_if_empty() -> None:
    conn = connect()
    try:
        init_schema(conn)
        n = conn.execute("SELECT COUNT(*) c FROM skus").fetchone()["c"]
        if n:
            # 既有库：确保回拨策略设置存在（全局唯一，缺省禁止回拨）。
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES ('clock_rewind_policy', ?)",
                (DEFAULT_POLICY,),
            )
            conn.commit()
            return

        skus = json.loads((FIXTURES / "skus.json").read_text(encoding="utf-8"))
        sales = json.loads((FIXTURES / "sales.json").read_text(encoding="utf-8"))
        now = _now()
        for s in skus:
            conn.execute(
                "INSERT INTO skus(sku_code, title, category, list_price, enabled) VALUES (?,?,?,?,1)",
                (s["sku_code"], s["title"], s["category"], float(s["list_price"])),
            )
            conn.execute(
                "INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), now, now),
            )
            conn.execute(
                "INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at, status, note) VALUES (?,?,NULL,?,?,?,NULL)",
                (s["sku_code"], float(s["list_price"]), now, now, HISTORY_ACTIVE),
            )
        for row in sales:
            conn.execute(
                "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
                (row["sale_no"], row["sku_code"], row["sold_at"], float(row["unit_price"]), int(row["qty"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('business_clock', ?)",
            ("2026-11-16T09:00:00Z",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_label', ?)",
            ("业务钟（可拨，用于判断改价是否到期；回拨受全局策略约束）",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_rewind_policy', ?)",
            (DEFAULT_POLICY,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_rewind_policy_label', ?)",
            (POLICY_LABELS[DEFAULT_POLICY],),
        )
        conn.commit()
    finally:
        conn.close()
