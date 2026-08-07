CREATE TABLE IF NOT EXISTS cash_flow (
    id SERIAL PRIMARY KEY,
    date TIMESTAMPTZ DEFAULT NOW(),
    description TEXT,
    category VARCHAR(30),  -- 'ad_spend', 'supplier_payment', 'customer_revenue',
                           -- 'processor_hold', 'refund', 'opening_balance', 'other'
    amount DECIMAL(12,2),  -- signed: credits positive, debits negative
    hold BOOLEAN DEFAULT FALSE,  -- money exists but is not yet spendable
    release_date TIMESTAMPTZ,
    experiment_id INT REFERENCES experiments(id),
    order_id INT REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_cash_flow_date ON cash_flow(date);
