CREATE TABLE IF NOT EXISTS experiment_events (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    event_type VARCHAR(40),
    detail TEXT,
    payload TEXT,             -- JSON snapshot
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_experiment ON experiment_events(experiment_id);
