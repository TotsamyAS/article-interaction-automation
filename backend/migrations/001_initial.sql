CREATE TABLE dataset (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    manifest TEXT NOT NULL CHECK (json_valid(manifest)),
    records TEXT NOT NULL CHECK (json_valid(records)),
    ground_truth TEXT NOT NULL CHECK (json_valid(ground_truth))
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    participant_code TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('experiment', 'practice')),
    sequence_no INTEGER NOT NULL,
    created_ms INTEGER NOT NULL,
    UNIQUE (participant_code, kind)
);
CREATE TABLE trials (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    position INTEGER NOT NULL,
    block_index INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('M1','M2','M3','M4','M5')),
    task_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','correct','incomplete')),
    started_ms INTEGER,
    ended_ms INTEGER,
    end_reason TEXT CHECK (end_reason IN ('correct','time_limit','attempt_limit')),
    UNIQUE (session_id, position),
    CHECK ((status = 'pending' AND started_ms IS NULL AND ended_ms IS NULL)
        OR (status = 'active' AND started_ms IS NOT NULL AND ended_ms IS NULL)
        OR (status IN ('correct','incomplete') AND started_ms IS NOT NULL AND ended_ms IS NOT NULL)),
    CHECK (ended_ms IS NULL OR ended_ms >= started_ms)
);
CREATE UNIQUE INDEX one_active_trial ON trials(session_id) WHERE status = 'active';
CREATE TABLE attempts (
    id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(id),
    request_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    query_json TEXT NOT NULL CHECK (json_valid(query_json)),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    correct INTEGER NOT NULL CHECK (correct IN (0,1)),
    started_ms INTEGER NOT NULL,
    finished_ms INTEGER NOT NULL,
    csv_text TEXT,
    UNIQUE (trial_id, request_id),
    UNIQUE (trial_id, ordinal)
);
CREATE TABLE events (
    trial_id TEXT NOT NULL REFERENCES trials(id),
    event_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    offset_ms INTEGER NOT NULL CHECK (offset_ms >= 0),
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    received_ms INTEGER NOT NULL,
    PRIMARY KEY (trial_id, event_id),
    UNIQUE (trial_id, sequence)
);
