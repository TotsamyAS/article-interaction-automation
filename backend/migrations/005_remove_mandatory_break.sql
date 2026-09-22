CREATE TABLE protocol_changes (
    id INTEGER PRIMARY KEY,
    changed_at_ms INTEGER NOT NULL,
    setting TEXT NOT NULL,
    previous_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    reason TEXT NOT NULL
);

-- Preserve the previous policy and cutover time for scientific exports.
-- No participant, session, trial, attempt or event is changed.
INSERT INTO protocol_changes (changed_at_ms, setting, previous_value, new_value, reason)
SELECT CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER), 'break_seconds',
       CAST(json_extract(manifest, '$.protocol.break_seconds') AS TEXT), '0',
       'Mandatory block breaks removed at user request, 2026-09-22'
FROM dataset WHERE json_extract(manifest, '$.protocol.break_seconds') > 0;

UPDATE dataset SET manifest = json_set(manifest, '$.protocol.break_seconds', 0)
WHERE json_extract(manifest, '$.protocol.break_seconds') > 0;
