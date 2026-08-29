-- Strikes and the gulag: a failing firm is suspended before it is destroyed.
--
-- Killing a firm outright threw away everything it had learned and left the
-- village one strategy poorer with no replacement. Six firms died that way in
-- nine days and all six kills turned out to be an artefact of counting fills
-- instead of bars. A system whose only answer to a bad run is death gets
-- quieter every time it is wrong.
--
-- Three strikes now. The first two suspend the firm for a number of *bars* —
-- no capital, no tokens, but the evolver keeps working on its genome. The
-- third terminates it and bankruptcy takes the estate.
--
-- **A separate table, not columns on `firms`.** Every migration here re-runs
-- on every `init-db`, so each one has to be idempotent, and SQLite has no
-- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. `CREATE TABLE IF NOT EXISTS` is
-- the shape that survives being applied twice. Same reasoning as 021.
--
-- `gulag_bars_left` counts bars the village actually observed, which is why
-- `gulag_last_bar` sits beside it: the loop ticks every sixty seconds, and a
-- counter decremented per tick would serve a twenty-bar sentence in twenty
-- minutes. A duration in bars is also not a duration on a clock — twenty bars
-- is twenty hours to the crypto desk and three trading days to the bonds desk
-- — and counting observed bars is the only definition that survives a
-- weekend, a holiday and a feed freeze.
--
-- `breach_open` makes a strike an episode rather than a state. A firm 27%
-- under water is in breach on every bar until it recovers; per-bar counting
-- gives firm_c_crypto 54 strikes on real history and terminates it in three
-- hours over one slump. Per episode gives it 2.

CREATE TABLE IF NOT EXISTS firm_strikes (
    firm_id INTEGER PRIMARY KEY,
    strikes INTEGER DEFAULT 0,
    gulag_bars_left INTEGER DEFAULT 0,
    gulag_last_bar TEXT,
    breach_open INTEGER DEFAULT 0,
    last_strike_reason TEXT,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
