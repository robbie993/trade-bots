-- See ../034_operator_orders.sql.

CREATE TABLE IF NOT EXISTS operator_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    dollars NUMERIC,
    quantity NUMERIC,
    sell_all INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',
    result TEXT DEFAULT '',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    confirmed_at TEXT,
    sent_at TEXT,
    done_at TEXT
);

CREATE INDEX IF NOT EXISTS operator_orders_status ON operator_orders (status, id);
