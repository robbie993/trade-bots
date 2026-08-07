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
