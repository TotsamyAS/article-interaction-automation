CREATE TABLE m3_interpretations (
    id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(id),
    request_id TEXT NOT NULL,
    user_text TEXT NOT NULL CHECK (length(user_text) BETWEEN 1 AND 2000),
    status TEXT NOT NULL CHECK (status IN ('pending','ok','provider_error','invalid_response','invalid_query')),
    requested_model TEXT NOT NULL,
    response_model TEXT,
    provider TEXT,
    system_fingerprint TEXT,
    prompt_version TEXT NOT NULL,
    raw_response TEXT,
    query_json TEXT CHECK (query_json IS NULL OR json_valid(query_json)),
    error_code TEXT,
    error_message TEXT,
    llm_ms REAL CHECK (llm_ms IS NULL OR llm_ms >= 0),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    started_ms INTEGER NOT NULL,
    finished_ms INTEGER,
    UNIQUE (trial_id, request_id)
);
CREATE INDEX m3_interpretations_trial ON m3_interpretations(trial_id, started_ms);
