-- Settings the village reads while it is running.
--
-- Everything else configurable in this system is an environment variable read
-- once at startup, which is right for things you decide before you begin:
-- which database, which feed, how much capital. It is wrong for the controls
-- on the wall.
--
-- Once the village runs on its own it is two processes — a tick loop and a web
-- console — and they do not share memory. A button in Mission Control that set
-- an environment variable would change nothing at all in the process that is
-- actually ticking. So the switches live here, where both can see them, and
-- the loop reads them on each pass rather than at startup.
--
-- Deliberately tiny and deliberately not a general key/value store for
-- application state: the ledger is the state. This holds operator intent —
-- is the village paused, is the bazaar open — and every value in it is
-- something a human toggled and can toggle back.
--
-- `updated_by` is here because the council can also write these, and a switch
-- that changed itself with no record of who did it is exactly the sort of
-- thing this repository refuses to have.

CREATE TABLE IF NOT EXISTS village_settings (
    key VARCHAR(60) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by VARCHAR(60),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
