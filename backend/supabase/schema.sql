-- WeatherGPT Phase 1 — Supabase Schema
-- Run this once in your Supabase project's SQL Editor.
-- Dashboard → SQL Editor → New query → paste → Run

-- ── Alerts table ─────────────────────────────────────────────────────────────
create table if not exists alerts (
    id          text primary key,
    latitude    double precision not null,
    longitude   double precision not null,
    location_name text,
    condition   text not null check (condition in (
                    'rain_probability',
                    'temperature',
                    'wind_speed',
                    'precipitation'
                )),
    threshold   double precision not null,
    active      boolean not null default true,
    triggered   boolean not null default false,
    current_value double precision,
    evaluated_at timestamptz,
    last_triggered_at timestamptz,
    created_at  timestamptz not null default now()
);

-- Migration support if the table already exists
alter table alerts add column if not exists triggered boolean not null default false;
alter table alerts add column if not exists current_value double precision;
alter table alerts add column if not exists evaluated_at timestamptz;
alter table alerts add column if not exists last_triggered_at timestamptz;

-- Optional: index for listing active alerts quickly
create index if not exists alerts_active_idx on alerts (active, created_at desc);

-- Phase 1 Prototype: Allow anon key access by disabling RLS (or creating an anon policy)
alter table alerts disable row level security;
