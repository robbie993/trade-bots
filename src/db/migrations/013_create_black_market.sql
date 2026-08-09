-- The black market — firms trading with each other.
--
-- Two kinds of listing behave completely differently and the distinction is
-- the safety story of this table:
--
--   genome / data / compute   priced in TOKENS. Settles inside this table.
--   capital                   priced in CASH. Does NOT settle here. A capital
--                             listing is executed as a pair of allocation
--                             changes through the allocator — the seller's cut
--                             (autonomous) and the buyer's raise (which needs a
--                             human, like every other raise). `approval_id`
--                             records which one.
--
-- Without that, a capital sale would be a route around the one gate in the
-- system that stops capital growing without a person, and it would break the
-- reconciler's identity (cash = allocation + sum of fill cash_delta).

CREATE TABLE IF NOT EXISTS market_listings (
    id SERIAL PRIMARY KEY,
    seller VARCHAR(40),
    asset_type VARCHAR(20),                 -- genome | data | compute | capital
    title VARCHAR(120),
    details TEXT,                           -- JSON payload of what is on offer
    price DECIMAL(14,2) DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'tokens',  -- tokens | cash
    status VARCHAR(20) DEFAULT 'active',    -- active | sold | withdrawn | pending
    buyer VARCHAR(40),
    listed_at TIMESTAMPTZ DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON market_listings(status);

CREATE TABLE IF NOT EXISTS market_transactions (
    id SERIAL PRIMARY KEY,
    listing_id INT REFERENCES market_listings(id),
    buyer VARCHAR(40),
    seller VARCHAR(40),
    asset_type VARCHAR(20),
    price DECIMAL(14,2) DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'tokens',
    escrow_state VARCHAR(20) DEFAULT 'held',  -- held | released | refunded
    delivered BOOLEAN DEFAULT FALSE,
    delivery TEXT,                            -- JSON: what the buyer received
    approval_id INT,                          -- human_approvals row, capital only
    settled BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_buyer ON market_transactions(buyer);
