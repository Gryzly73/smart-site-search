PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    client_request_id TEXT NOT NULL,
    creates_session INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    mode TEXT,
    response_json TEXT,
    UNIQUE (session_id, client_request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS requests_create_id
    ON requests(client_request_id) WHERE creates_session = 1;
CREATE UNIQUE INDEX IF NOT EXISTS requests_busy
    ON requests(session_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS requests_completed ON requests(completed_at, mode);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    UNIQUE (request_id, seq)
);
CREATE INDEX IF NOT EXISTS events_session_time ON events(session_id, occurred_at);
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    start_json TEXT NOT NULL,
    usage_json TEXT
);
CREATE INDEX IF NOT EXISTS llm_calls_request ON llm_calls(request_id);
