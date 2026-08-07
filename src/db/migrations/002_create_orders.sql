CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    external_id VARCHAR(64) UNIQUE,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    customer_paid DECIMAL(10,2),
    supplier_cost DECIMAL(10,2),
    shipping_cost DECIMAL(10,2),
    ad_cost DECIMAL(10,2),                  -- attributed ad spend for this order
    payment_processor_fee DECIMAL(10,2),
    refunded BOOLEAN DEFAULT FALSE,
    refund_date TIMESTAMPTZ,
    chargeback BOOLEAN DEFAULT FALSE,
    delivered BOOLEAN DEFAULT FALSE,
    delivery_date TIMESTAMPTZ,
    days_to_delivery INT
);

CREATE INDEX IF NOT EXISTS idx_orders_experiment ON orders(experiment_id);
