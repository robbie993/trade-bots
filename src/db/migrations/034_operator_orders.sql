-- Orders the operator gave the village in the chat, for their own paper desk.
--
-- The village writes an order under its reply when the operator tells it to
-- trade (`status` 'proposed'); the operator taps Confirm on the website
-- ('confirmed'); the desk's bot (`bots/operator_desk.py`) hands it to the tick
-- as an ordinary proposal ('sent'), where it meets the same risk manager,
-- conscience, paper venue and ledger as every other firm's order, and the
-- outcome is copied back ('filled' or 'blocked'). Paper only: the desk is a
-- paper firm, `firm_operator_desk`.

CREATE TABLE IF NOT EXISTS operator_orders (
    id SERIAL PRIMARY KEY,
    message_id INTEGER,
    side VARCHAR(10) NOT NULL,
    symbol VARCHAR(40) NOT NULL,
    dollars NUMERIC,
    quantity NUMERIC,
    sell_all INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'proposed',
    result TEXT DEFAULT '',
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    confirmed_at TEXT,
    sent_at TEXT,
    done_at TEXT
);

CREATE INDEX IF NOT EXISTS operator_orders_status ON operator_orders (status, id);
