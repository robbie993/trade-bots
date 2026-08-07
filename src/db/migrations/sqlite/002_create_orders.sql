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
