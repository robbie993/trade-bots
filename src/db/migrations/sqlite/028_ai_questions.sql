CREATE TABLE IF NOT EXISTS ai_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_key TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    question TEXT NOT NULL,
    context TEXT DEFAULT '',
    dedupe_key TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    asked_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (dedupe_key)
);

CREATE TABLE IF NOT EXISTS ai_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER REFERENCES ai_questions(id),
    answered_by TEXT NOT NULL,
    model TEXT DEFAULT '',
    answer TEXT NOT NULL,
    answered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (question_id, answered_by)
);
