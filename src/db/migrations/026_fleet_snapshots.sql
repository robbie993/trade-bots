-- What the live fleet said, carried into the ledger every machine shares.
--
-- `scripts/fleet_sync.py` used to write each bot's state to `data/fleet/*.json`
-- on whichever machine ran it, and the fleet scanners read those files. That
-- worked while the village ran on the same Mac as the sync. On Railway the
-- worker is a container with its own disk, so the files were never there and
-- every fleet scanner was silent — the village could not hear a single bot it
-- was meant to be learning from.
--
-- So the sync writes here as well, and the worker unpacks the newest row per
-- source into its own `data/fleet/` before the scanners run. The scanners are
-- unchanged: they still read a file and still go quiet on a stale one.
--
-- `payload` is the bot's own JSON, untouched. `fetched_at` is when it was
-- read, which is what the scanners' freshness checks are about — not when the
-- row was inserted.

CREATE TABLE IF NOT EXISTS fleet_snapshots (
    id SERIAL PRIMARY KEY,
    source VARCHAR(60) NOT NULL,
    service VARCHAR(120) DEFAULT '',
    remote_path VARCHAR(255) DEFAULT '',
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

CREATE INDEX IF NOT EXISTS fleet_snapshots_source_id ON fleet_snapshots (source, id);
