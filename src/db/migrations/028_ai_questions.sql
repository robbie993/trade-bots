-- Questions the firms ask other minds, and what they were told.
--
-- `src/trading/council_of_ais.py` kept this queue as files under
-- `data/ai_queue/`. On Railway those files would be inside the worker's
-- container, where nothing that can answer them can see them. So the queue
-- lives in the ledger every machine shares: the worker asks, the operator's PC
-- answers (Claude Code, and later the browser-driven models), and the answer is
-- delivered back to the asking firm's memory as outside advice.
--
-- One question can have several answers, one per mind, so the answers are
-- their own table. `dedupe_key` is how a firm asks a thing once: a heir asks
-- about its predecessor once, a weekly review once a week.

CREATE TABLE IF NOT EXISTS ai_questions (
    id SERIAL PRIMARY KEY,
    firm_key VARCHAR(80) DEFAULT '',
    topic VARCHAR(40) DEFAULT '',
    question TEXT NOT NULL,
    context TEXT DEFAULT '',
    dedupe_key VARCHAR(200) NOT NULL,
    status VARCHAR(20) DEFAULT 'open',
    asked_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    UNIQUE (dedupe_key)
);

CREATE TABLE IF NOT EXISTS ai_answers (
    id SERIAL PRIMARY KEY,
    question_id INTEGER REFERENCES ai_questions(id),
    answered_by VARCHAR(120) NOT NULL,
    model VARCHAR(120) DEFAULT '',
    answer TEXT NOT NULL,
    answered_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    UNIQUE (question_id, answered_by)
);
