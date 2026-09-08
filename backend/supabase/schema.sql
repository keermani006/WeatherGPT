-- WeatherGPT Phase 2 — Supabase Schema Migration
-- Run this in your Supabase project's SQL Editor.
-- Dashboard → SQL Editor → New query → paste → Run
--
-- This script is ADDITIVE — it will not destroy existing alert data.
-- Existing rows will have user_id = NULL after migration.
-- These orphaned rows are harmless; they are invisible to authenticated users
-- (RLS hides them) but remain accessible to the service-role backend for evaluation.
-- To clean them up: DELETE FROM alerts WHERE user_id IS NULL;

-- ── Phase 1 base table (idempotent) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id                text PRIMARY KEY,
    latitude          double precision NOT NULL,
    longitude         double precision NOT NULL,
    location_name     text,
    condition         text NOT NULL CHECK (condition IN (
                          'rain_probability',
                          'temperature',
                          'wind_speed',
                          'precipitation'
                      )),
    threshold         double precision NOT NULL,
    active            boolean NOT NULL DEFAULT true,
    triggered         boolean NOT NULL DEFAULT false,
    current_value     double precision,
    evaluated_at      timestamptz,
    last_triggered_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- ── Phase 1 → Phase 2 column migrations (idempotent) ─────────────────────────
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS triggered         boolean NOT NULL DEFAULT false;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS current_value     double precision;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS evaluated_at      timestamptz;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_triggered_at timestamptz;

-- ── Phase 2: user ownership ───────────────────────────────────────────────────
-- user_id is nullable to preserve any existing development/test rows.
-- New inserts from the authenticated API always include user_id.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id);

-- ── Indexes ───────────────────────────────────────────────────────────────────
-- Index for scheduler: listing active alerts
CREATE INDEX IF NOT EXISTS alerts_active_idx      ON alerts (active, created_at DESC);
-- Index for user-scoped queries
CREATE INDEX IF NOT EXISTS alerts_user_id_idx     ON alerts (user_id);
-- Composite index for common query: user's active alerts
CREATE INDEX IF NOT EXISTS alerts_user_active_idx ON alerts (user_id, active);
-- Index for time-ordered listing
CREATE INDEX IF NOT EXISTS alerts_created_at_idx  ON alerts (created_at DESC);

-- ── Row Level Security ────────────────────────────────────────────────────────
-- The service-role key (used by the FastAPI backend) bypasses RLS automatically.
-- The anon/authenticated JWT users are restricted by these policies.
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

-- Drop and recreate policies to ensure they are up to date
DROP POLICY IF EXISTS "users_select_own" ON alerts;
DROP POLICY IF EXISTS "users_insert_own" ON alerts;
DROP POLICY IF EXISTS "users_update_own" ON alerts;
DROP POLICY IF EXISTS "users_delete_own" ON alerts;

CREATE POLICY "users_select_own" ON alerts
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "users_insert_own" ON alerts
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "users_update_own" ON alerts
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "users_delete_own" ON alerts
    FOR DELETE USING (auth.uid() = user_id);

-- ── Verification query ────────────────────────────────────────────────────────
-- Run after migration to verify RLS and indexes:
-- SELECT schemaname, tablename, rowsecurity FROM pg_tables WHERE tablename = 'alerts';
-- SELECT indexname FROM pg_indexes WHERE tablename = 'alerts';
