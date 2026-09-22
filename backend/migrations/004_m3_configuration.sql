ALTER TABLE m3_interpretations ADD COLUMN prompt_sha256 TEXT;
ALTER TABLE m3_interpretations ADD COLUMN temperature REAL CHECK (temperature IS NULL OR (temperature >= 0 AND temperature <= 2));
