CREATE TABLE m4_transcriptions (
    id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(id),
    request_id TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    audio_bytes INTEGER NOT NULL CHECK (audio_bytes > 0),
    audio_duration_ms INTEGER NOT NULL CHECK (audio_duration_ms > 0),
    requested_model TEXT NOT NULL,
    compute_type TEXT NOT NULL,
    requested_language TEXT NOT NULL,
    detected_language TEXT,
    language_probability REAL CHECK (language_probability IS NULL OR (language_probability >= 0 AND language_probability <= 1)),
    transcript TEXT NOT NULL,
    asr_ms REAL NOT NULL CHECK (asr_ms >= 0),
    started_ms INTEGER NOT NULL,
    finished_ms INTEGER NOT NULL,
    UNIQUE (trial_id, request_id)
);
CREATE INDEX m4_transcriptions_trial ON m4_transcriptions(trial_id, started_ms);

-- The experiment originally used the browser's server-backed Web Speech API.
-- Record the cutover because ASR is part of the M4 experimental protocol.
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'm4_asr',
       'browser Web Speech API (ru-RU)',
       '{"engine":"faster-whisper","engine_version":"1.2.1","model":"large-v3-turbo","device":"cpu","compute_type":"int8","language":"ru","beam_size":5,"audio_limit_seconds":45}',
       'Web Speech API is unavailable from the deployment region without VPN; M4 moved to self-hosted ASR to avoid VPN as an experimental confound'
FROM dataset
WHERE json_type(manifest, '$.m4_asr') IS NULL;

UPDATE dataset
SET manifest = json_set(
    manifest,
    '$.m4_asr',
    json('{"engine":"faster-whisper","engine_version":"1.2.1","model":"large-v3-turbo","device":"cpu","compute_type":"int8","language":"ru","beam_size":5,"audio_limit_seconds":45}')
)
WHERE json_type(manifest, '$.m4_asr') IS NULL;
