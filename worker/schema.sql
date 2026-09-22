-- Centurion cloud sync, D1 schema. Mirrors what the PC server kept in state.json.
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- one row per (storage key, first-level property), exactly the unit the app syncs
CREATE TABLE IF NOT EXISTS entries (
  eid TEXT PRIMARY KEY,          -- k + "\0" + s
  k   TEXT NOT NULL,
  s   TEXT NOT NULL,
  v   TEXT,                      -- JSON, null when deleted
  d   INTEGER NOT NULL DEFAULT 0,
  rev INTEGER NOT NULL,
  by  TEXT,
  at  TEXT
);
CREATE INDEX IF NOT EXISTS entries_rev ON entries (rev);

-- Oura OAuth2 tokens. The client id/secret are Worker secrets, not rows.
CREATE TABLE IF NOT EXISTS oura (
  user          TEXT PRIMARY KEY,
  access_token  TEXT NOT NULL,
  refresh_token TEXT,
  expires_at    REAL,
  scope         TEXT,
  linked_at     TEXT
);
