-- M4 keeps the same speech -> transcript -> RouterAI -> executor design, but
-- switches the self-hosted ASR implementation from faster-whisper to the
-- smaller Russian GigaAM-v3 e2e RNN-T model. Existing trial data remains valid;
-- the change is recorded explicitly in the protocol history.
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'm4_asr',
       json_extract(manifest, '$.m4_asr'),
       '{"engine":"gigaam-v3","engine_version":"3","model":"ai-sage/GigaAM-v3","revision":"e2e_rnnt","device":"cpu","compute_type":"float32","language":"ru","audio_limit_seconds":20}',
       'M4 ASR moved to GigaAM-v3/e2e_rnnt; model weights are baked into the backend image during docker build so participant requests never download models'
FROM dataset
WHERE json_extract(manifest, '$.m4_asr.engine') = 'faster-whisper';

UPDATE dataset
SET manifest = json_set(
    manifest,
    '$.m4_asr',
    json('{"engine":"gigaam-v3","engine_version":"3","model":"ai-sage/GigaAM-v3","revision":"e2e_rnnt","device":"cpu","compute_type":"float32","language":"ru","audio_limit_seconds":20}')
)
WHERE json_extract(manifest, '$.m4_asr.engine') = 'faster-whisper';
