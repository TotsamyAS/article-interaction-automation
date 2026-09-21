CREATE TABLE access_principals (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('participant', 'researcher')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0)
);
