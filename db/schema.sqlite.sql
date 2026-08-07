-- MVV Phase 1 — SQLite mirror of db/schema.postgres.sql.
--
-- SQLite is the zero-setup backend used for local runs and the test suite.
-- Semantics are identical; only the type spellings differ (SERIAL ->
-- INTEGER PRIMARY KEY AUTOINCREMENT, TIMESTAMPTZ -> TEXT ISO-8601,
-- DECIMAL -> NUMERIC read back through Decimal in mvv.db).
--
-- Money is written as TEXT-free NUMERIC and always converted with
-- decimal.Decimal(str(value)) on read, so no binary float ever reaches a
-- cash figure.

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT UNIQUE,
    product_name TEXT,
    source_platform TEXT,
    supplier TEXT,
    product_url TEXT,
    unit_cost NUMERIC,
    selling_price NUMERIC,
    margin_pct NUMERIC,
    margin_abs NUMERIC,

    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    sessions INTEGER DEFAULT 0,

    orders INTEGER DEFAULT 0,
    revenue NUMERIC DEFAULT 0,
    ad_spend NUMERIC DEFAULT 0,

    ctr NUMERIC DEFAULT 0,
    conversion_rate NUMERIC DEFAULT 0,
    cac NUMERIC DEFAULT 0,
    aov NUMERIC DEFAULT 0,
    contribution_margin NUMERIC DEFAULT 0,

    refunds INTEGER DEFAULT 0,
    chargebacks INTEGER DEFAULT 0,
    refund_rate NUMERIC DEFAULT 0,
    chargeback_rate NUMERIC DEFAULT 0,
    avg_delivery_days NUMERIC DEFAULT 0,

    minimum_orders INTEGER DEFAULT 50,
    minimum_sessions INTEGER DEFAULT 100,
    minimum_impressions INTEGER DEFAULT 1000,
    meets_sample_gate INTEGER DEFAULT 0,

    ads_paused INTEGER DEFAULT 1,
    daily_ad_budget NUMERIC DEFAULT 0,

    status TEXT DEFAULT 'discovered',
    kill_reason TEXT,
    pending_kill_reason TEXT,
    kill_override_reason TEXT,
    killed_at TEXT,
    scaled_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER REFERENCES experiments(id),
    external_id TEXT UNIQUE,
    order_date TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    customer_paid NUMERIC,
    supplier_cost NUMERIC,
    shipping_cost NUMERIC,
    ad_cost NUMERIC,
    payment_processor_fee NUMERIC,
    refunded INTEGER DEFAULT 0,
    refund_date TEXT,
    chargeback INTEGER DEFAULT 0,
    delivered INTEGER DEFAULT 0,
    delivery_date TEXT,
    days_to_delivery INTEGER
);

CREATE INDEX IF NOT EXISTS idx_orders_experiment ON orders(experiment_id);

CREATE TABLE IF NOT EXISTS cash_flow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    description TEXT,
    category TEXT,
    amount NUMERIC,
    hold INTEGER DEFAULT 0,
    release_date TEXT,
    experiment_id INTEGER REFERENCES experiments(id),
    order_id INTEGER REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_cash_flow_date ON cash_flow(date);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    experiment_id INTEGER REFERENCES experiments(id),
    amount NUMERIC,
    reason TEXT,
    details TEXT,
    status TEXT DEFAULT 'pending',
    requested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    decided_at TEXT,
    decided_by TEXT,
    decision_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS experiment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER REFERENCES experiments(id),
    event_type TEXT,
    detail TEXT,
    payload TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_events_experiment ON experiment_events(experiment_id);
