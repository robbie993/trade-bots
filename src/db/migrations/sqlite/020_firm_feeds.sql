-- Mirror of 020_firm_feeds.sql for SQLite. See that file for why a record
-- measured on a random walk is not evidence about real money.

CREATE TABLE IF NOT EXISTS firm_feeds (
    firm_id INTEGER NOT NULL,
    priced_by TEXT NOT NULL,
    bars INTEGER DEFAULT 0,
    first_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (firm_id, priced_by)
);
