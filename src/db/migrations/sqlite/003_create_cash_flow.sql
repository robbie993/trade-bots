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
