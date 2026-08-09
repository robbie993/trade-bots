-- The flow stream — what the tick is doing, as it does it.
--
-- Written through the database rather than an in-process bus on purpose: the
-- normal way to run this is `trade run` in one terminal and the dashboard in
-- another, which are two processes. A queue inside the web server would show
-- an empty diagram for exactly the case the diagram is for.
--
-- Deliberately cheap and deliberately disposable. Roughly fifteen rows per
-- tick, no money in any column, and `FlowRecorder` prunes to the most recent
-- few hundred. If this table were dropped mid-run nothing would break: the
-- ledger does not read it, and the tick does not check whether the write
-- succeeded.

CREATE TABLE IF NOT EXISTS flow_events (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40),                     -- one tick, so a viewer can group them
    node VARCHAR(30),                       -- where the activity is
    edge VARCHAR(60),                       -- "from>to", when something moved
    kind VARCHAR(20) DEFAULT 'ok',          -- ok | blocked | refused | alarm
    label VARCHAR(120),
    firm VARCHAR(40),
    detail TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flow_events_run ON flow_events(run_id);
