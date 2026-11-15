# -*- coding: utf-8 -*-
"""SQLite 后台:询价单、发信记录、收到的报价(多轮)、比价、催办。"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    perf_score  REAL NOT NULL DEFAULT 80   -- 历史履约分 0-100
);

CREATE TABLE IF NOT EXISTS inquiries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,      -- 唯一编号 RFQ-2026-0001
    title       TEXT NOT NULL,
    deadline    TEXT NOT NULL,             -- ISO 时间
    status      TEXT NOT NULL DEFAULT 'new',  -- new/sent/closed
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inquiry_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiries(id),
    name        TEXT NOT NULL,
    qty         REAL NOT NULL,
    unit        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inquiry_suppliers (
    inquiry_id  INTEGER NOT NULL REFERENCES inquiries(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    UNIQUE (inquiry_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS sent_mails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiries(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    kind        TEXT NOT NULL,             -- inquiry / reminder
    message_id  TEXT NOT NULL,
    to_addr     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  INTEGER REFERENCES inquiries(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    message_id  TEXT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    round       INTEGER NOT NULL DEFAULT 1,
    raw_path    TEXT
);

CREATE TABLE IF NOT EXISTS quotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reply_id    INTEGER NOT NULL REFERENCES replies(id),
    inquiry_id  INTEGER NOT NULL REFERENCES inquiries(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    round       INTEGER NOT NULL,
    source      TEXT NOT NULL,             -- body / excel / pdf
    is_current  INTEGER NOT NULL DEFAULT 1,  -- 同一供应商只保留最后一轮为当前
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id        INTEGER NOT NULL REFERENCES quotes(id),
    item_name       TEXT NOT NULL,       -- 报价里的原始品名
    matched_item_id INTEGER REFERENCES inquiry_items(id),
    qty             REAL,
    unit_price      REAL,
    delivery_days   REAL,
    amount          REAL,
    match_score     REAL NOT NULL DEFAULT 0,
    needs_review    INTEGER NOT NULL DEFAULT 0   -- 1 = 标黄人工确认
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  INTEGER NOT NULL REFERENCES inquiries(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    sent_at     TEXT NOT NULL,
    UNIQUE (inquiry_id, supplier_id)     -- 每张单每家只催一次
);

CREATE INDEX IF NOT EXISTS idx_replies_inq  ON replies(inquiry_id, supplier_id);
CREATE INDEX IF NOT EXISTS idx_quotes_inq   ON quotes(inquiry_id, supplier_id, is_current);
CREATE INDEX IF NOT EXISTS idx_qitems_quote ON quote_items(quote_id);
"""


def connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA)
    return conn
