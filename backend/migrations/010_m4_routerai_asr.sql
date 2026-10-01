-- Move M4 ASR from local GigaAM/faster-whisper to RouterAI Audio Transcriptions.
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'm4_asr',
       json_extract(manifest, '$.m4_asr'),
       '{"engine":"routerai","engine_version":"audio-transcriptions-v1","provider":"routerai","model":"nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b","language":"ru","audio_limit_seconds":20}',
       'M4 ASR moved to RouterAI because the production VM is too small for low-latency self-hosted inference; the model is selected in config.json'
FROM dataset
WHERE json_extract(manifest, '$.m4_asr.engine') IN ('gigaam-v3', 'faster-whisper');

UPDATE dataset
SET manifest = json_set(
    manifest,
    '$.m4_asr',
    json('{"engine":"routerai","engine_version":"audio-transcriptions-v1","provider":"routerai","model":"nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b","language":"ru","audio_limit_seconds":20}')
)
WHERE json_extract(manifest, '$.m4_asr.engine') IN ('gigaam-v3', 'faster-whisper');
