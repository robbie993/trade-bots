-- The sandbox — alliances, betrayal, espionage.
--
-- These two tables are the ONLY tables the sandbox may write, and that is
-- enforced in code by src/trading/sandbox/guard.py rather than by convention:
-- the sandbox is handed a store wrapper that raises on any other table and on
-- any read-write method of the real ledger.
--
-- Why: the brokerage cuts capital on a score, and the kill switch's reason has
-- to be reproducible from stored metrics. If one firm could move another's
-- equity, a drawdown would no longer be a fact about that firm's strategy, the
-- allocator would punish victims, and every kill reason would become a guess.
-- The intrigue is therefore real as a game and inert as far as the money is
-- concerned: sabotage moves `shadow_equity` here and nothing there.

CREATE TABLE IF NOT EXISTS alliances (
    id SERIAL PRIMARY KEY,
    name VARCHAR(80) UNIQUE NOT NULL,
    members TEXT,                           -- JSON list of firm keys
    founder VARCHAR(40),
    pact TEXT,                              -- what the members agreed
    standing DECIMAL(9,2) DEFAULT 100,
    status VARCHAR(20) DEFAULT 'active',    -- active | broken | dissolved
    formed_at TIMESTAMPTZ DEFAULT NOW(),
    broken_at TIMESTAMPTZ,
    broken_by VARCHAR(40)
);

CREATE TABLE IF NOT EXISTS sandbox_events (
    id SERIAL PRIMARY KEY,
    season INT DEFAULT 1,
    event_type VARCHAR(40),                 -- alliance | betrayal | espionage | sabotage
    actor VARCHAR(40),
    target VARCHAR(40),
    alliance_id INT REFERENCES alliances(id),
    detail TEXT,
    payload TEXT,                           -- JSON, including anything "stolen"
    shadow_equity DECIMAL(14,2) DEFAULT 0,  -- the sandbox's own scoreboard
    reputation_delta DECIMAL(9,2) DEFAULT 0,
    real_effect BOOLEAN DEFAULT FALSE,      -- always false; asserted by tests
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sandbox_events_actor ON sandbox_events(actor);
CREATE INDEX IF NOT EXISTS idx_sandbox_events_type ON sandbox_events(event_type);
