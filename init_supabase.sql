-- ============================================================
-- HCL Bot — Tabelas Supabase
-- Rode no SQL Editor do Supabase Dashboard
-- ============================================================

-- 1. PLAYERS
CREATE TABLE IF NOT EXISTS players (
  id            TEXT PRIMARY KEY,
  username      TEXT,
  name          TEXT,
  tier          TEXT DEFAULT 'F',
  wins          INT DEFAULT 0,
  losses        INT DEFAULT 0,
  kills         INT DEFAULT 0,
  deaths        INT DEFAULT 0,
  region        TEXT,
  platform      TEXT DEFAULT 'PC',
  affiliation   TEXT,
  available     BOOLEAN DEFAULT FALSE,
  hidden        BOOLEAN DEFAULT FALSE,
  previous_tier TEXT,
  avatar        TEXT,
  avatar_data   TEXT,
  match_history JSONB DEFAULT '[]'::jsonb,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2. MATCHES
CREATE TABLE IF NOT EXISTS matches (
  id              TEXT PRIMARY KEY,
  event           TEXT,
  played_at       TIMESTAMPTZ,
  side1_playerids JSONB DEFAULT '[]'::jsonb,
  side2_playerids JSONB DEFAULT '[]'::jsonb,
  side1_score     INT DEFAULT 0,
  side2_score     INT DEFAULT 0,
  winning_side    INT DEFAULT 0,
  status          TEXT DEFAULT 'completed',
  recording_url   TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 3. EVENTS
CREATE TABLE IF NOT EXISTS events (
  id            TEXT PRIMARY KEY,
  name          TEXT,
  date          TIMESTAMPTZ,
  completed     BOOLEAN DEFAULT FALSE,
  completed_at  TIMESTAMPTZ,
  is_tournament BOOLEAN DEFAULT FALSE,
  description   TEXT,
  location      TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 4. SYNC LOG (controla quando foi o último sync)
CREATE TABLE IF NOT EXISTS sync_log (
  id          SERIAL PRIMARY KEY,
  endpoint    TEXT NOT NULL,
  rows_synced INT DEFAULT 0,
  synced_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_players_tier ON players(tier);
CREATE INDEX IF NOT EXISTS idx_players_affiliation ON players(affiliation);
CREATE INDEX IF NOT EXISTS idx_players_region ON players(region);
CREATE INDEX IF NOT EXISTS idx_matches_played_at ON matches(played_at DESC);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
