-- Strikes and the gulag: a failing firm is suspended before it is destroyed.
-- See the SQLite mirror for the full reasoning.
--
-- Killing outright threw away everything a firm had learned, and all six kills
-- that actually happened were an artefact of counting fills instead of bars.
-- Three strikes now: the first two suspend for a number of bars while the
-- evolver keeps working on the genome, the third terminates.
--
-- A separate table rather than columns on `firms`, because every migration
-- here re-runs on every init and this shape is idempotent on both dialects.
--
-- `gulag_bars_left` counts bars the village observed — hence `gulag_last_bar`,
-- since a per-tick decrement would serve a twenty-bar sentence in twenty
-- minutes. `breach_open` makes a strike an episode rather than a state: per-bar
-- counting gives firm_c_crypto 54 strikes on real history, per episode gives 2.

CREATE TABLE IF NOT EXISTS firm_strikes (
    firm_id INTEGER PRIMARY KEY,
    strikes INTEGER DEFAULT 0,
    gulag_bars_left INTEGER DEFAULT 0,
    gulag_last_bar TEXT,
    breach_open INTEGER DEFAULT 0,
    last_strike_reason TEXT,
    updated_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);
