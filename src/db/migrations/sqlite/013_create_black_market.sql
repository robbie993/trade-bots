CREATE TABLE IF NOT EXISTS market_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller TEXT,
    asset_type TEXT,
    title TEXT,
    details TEXT,
    price NUMERIC DEFAULT 0,
    currency TEXT DEFAULT 'tokens',
    status TEXT DEFAULT 'active',
    buyer TEXT,
    listed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    settled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON market_listings(status);

CREATE TABLE IF NOT EXISTS market_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER REFERENCES market_listings(id),
    buyer TEXT,
    seller TEXT,
    asset_type TEXT,
    price NUMERIC DEFAULT 0,
    currency TEXT DEFAULT 'tokens',
    escrow_state TEXT DEFAULT 'held',
    delivered INTEGER DEFAULT 0,
    delivery TEXT,
    approval_id INTEGER,
    settled INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_buyer ON market_transactions(buyer);
