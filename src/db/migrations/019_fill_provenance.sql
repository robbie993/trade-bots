-- Which feed priced the book.
--
-- A village ran for weeks on the synthetic feed, bought positions at invented
-- prices, and was then pointed at Alpaca. Nothing was corrupted: the cash
-- identities held, `reconcile` said yes, and the console reported $20,396,997
-- of equity against $289,152 of capital ever deployed.
--
-- The arithmetic was right and the number was fiction. A firm that spent
-- $10,000 on BTC-USD at a synthetic price of about $100 a unit holds a hundred
-- units; mark those at the real price and it "owns" ten million dollars. The
-- quantities were bought in one price universe and valued in another.
--
-- That is worse than the feed going dark, because a dark feed announces itself
-- — no bars, no prices, an obvious hole. This announces nothing. Every score,
-- every drawdown, every kill and every allocation downstream was computed from
-- it, and six firms were held dead on drawdowns that were arithmetic across
-- mismatched prices.
--
-- So a position remembers the feed that built it. With that the evaluator can
-- ask the only question that matters: **was this book built on the feed we are
-- marking it with?** When the answer is no, the numbers are not measurements,
-- and nothing irreversible may be decided from them — the same rule the
-- blind-feed guard follows, for the same reason.
--
-- A table rather than a column on `fills`, because every migration here re-runs
-- on every start and must be idempotent, and SQLite has no
-- `ADD COLUMN IF NOT EXISTS`. Keyed by (firm, symbol) rather than by fill,
-- because the question is about a position, not about a transaction.
--
-- A position with no row is one opened before anyone recorded this — honestly
-- unknown rather than falsely attributed — and the guard treats unknown as
-- unknown rather than as a mismatch.

CREATE TABLE IF NOT EXISTS position_provenance (
    firm_id INTEGER NOT NULL,
    symbol VARCHAR(40) NOT NULL,
    priced_by VARCHAR(40) NOT NULL,     -- the feed's name at the time
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (firm_id, symbol)
);
