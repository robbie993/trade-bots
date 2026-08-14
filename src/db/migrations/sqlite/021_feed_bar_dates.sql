-- Mirror of 021_feed_bar_dates.sql for SQLite. See that file for why a
-- counter incremented once per tick is not a count of market bars.

CREATE TABLE IF NOT EXISTS firm_feed_bars (
    firm_id INTEGER NOT NULL,
    priced_by TEXT NOT NULL,
    bar_date TEXT NOT NULL,
    recorded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (firm_id, priced_by, bar_date)
);

CREATE INDEX IF NOT EXISTS idx_firm_feed_bars_lookup
    ON firm_feed_bars (firm_id, priced_by);

DROP TABLE IF EXISTS firm_feeds;
