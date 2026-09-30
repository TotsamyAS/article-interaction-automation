-- Terminal observations keep their original protocol and penalties.
ALTER TABLE trials ADD COLUMN trial_limit_seconds INTEGER NOT NULL DEFAULT 300 CHECK (trial_limit_seconds > 0);
ALTER TABLE trials ADD COLUMN attempt_limit INTEGER NOT NULL DEFAULT 5 CHECK (attempt_limit > 0);
ALTER TABLE trials ADD COLUMN wording_version TEXT NOT NULL DEFAULT 'legacy';
UPDATE trials SET
    trial_limit_seconds = COALESCE((SELECT json_extract(manifest, '$.protocol.trial_limit_seconds') FROM dataset), 300),
    attempt_limit = COALESCE((SELECT json_extract(manifest, '$.protocol.attempt_limit') FROM dataset), 5);
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'trial_limits',
       json_object('trial_limit_seconds', json_extract(manifest, '$.protocol.trial_limit_seconds'),
                   'attempt_limit', json_extract(manifest, '$.protocol.attempt_limit')),
       '{"trial_limit_seconds":1500,"attempt_limit":25}',
       '25 minutes / 25 attempts; pending and active trials upgraded; terminal trials preserved'
FROM dataset;
UPDATE trials SET trial_limit_seconds = 1500, attempt_limit = 25, wording_version = 'ru-project-terms-v2' WHERE status IN ('pending', 'active');
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'interface_wording', 'legacy', 'ru-project-terms-v2',
       'Epic / sprint display terms changed; original dataset and canonical queries preserved'
FROM dataset;
UPDATE dataset SET manifest = json_set(manifest, '$.protocol.trial_limit_seconds', 1500, '$.protocol.attempt_limit', 25);
