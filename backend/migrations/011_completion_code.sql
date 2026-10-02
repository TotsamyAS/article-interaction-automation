ALTER TABLE sessions ADD COLUMN completion_code TEXT;
CREATE UNIQUE INDEX sessions_completion_code_unique
    ON sessions(completion_code)
    WHERE completion_code IS NOT NULL;
