# WeatherGPT

> **Conversational AI for Hyper-Localized Weather Intelligence, Corridor Routing, Cargo Protection, and Climate Analytics**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-16.3-black?style=flat&logo=next.js&logoColor=white)](https://nextjs.org)
[![React](https://img.shields.io/badge/React-19.2-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v4-38B2AC?style=flat&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![Groq](https://img.shields.io/badge/LLM-Groq%20Qwen%203.8--27B-orange?style=flat)](https://groq.com)
[![Open-Meteo](https://img.shields.io/badge/Weather-Open--Meteo-blue?style=flat)](https://open-meteo.com)
[![Redis](https://img.shields.io/badge/Cache-Redis%20Cloud-DC382D?style=flat&logo=redis&logoColor=white)](https://redis.io)
[![Supabase](https://img.shields.io/badge/Backend-Supabase-3ECF8E?style=flat&logo=supabase&logoColor=white)](https://supabase.com)

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Key Functionalities & Features](#key-functionalities--features)
  - [1. Conversational AI Weather Assistant](#1-conversational-ai-weather-assistant)
  - [2. Travel Corridor Weather & Agricultural Cargo Recommendations](#2-travel-corridor-weather--agricultural-cargo-recommendations)
  - [3. Proactive Weather Alerts & Threshold Monitoring](#3-proactive-weather-alerts--threshold-monitoring)
  - [4. Climate Projections & Decadal Trends](#4-climate-projections--decadal-trends)
  - [5. Location Intelligence & Geocoding](#5-location-intelligence--geocoding)
  - [6. High-Performance Redis Cloud Caching & Resilience](#6-high-performance-redis-cloud-caching--resilience)
  - [7. Authentication & User Session Isolation](#7-authentication--user-session-isolation)
- [Tech Stack](#tech-stack)
- [Project Directory Structure](#project-directory-structure)
- [API Endpoints Reference](#api-endpoints-reference)
- [Environment Variables](#environment-variables)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Backend Setup](#backend-setup)
  - [Frontend Setup](#frontend-setup)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [License](#license)

---

## Overview

**WeatherGPT** is a production-grade, full-stack weather intelligence platform engineered to transform raw meteorological data into actionable natural-language insights, travel safety advisories, and agricultural logistics protection.

Traditional weather apps provide static dashboards with fragmented numbers. WeatherGPT bridges live atmospheric datasets (via Open-Meteo) and high-speed LLM reasoning (via Groq Qwen 3.8-27B) into an interactive copilot that maintains conversational state, understands travel corridors, predicts transit risks, and alerts users before severe conditions strike.

### Core Philosophy: Zero Hallucination
The backend remains the authoritative source of truth for all meteorological measurements. The LLM receives pre-fetched, normalized weather observations and route waypoints; it never invents numbers or queries external APIs directly.

---

## System Architecture

```
                                  ┌─────────────────────────────────┐
                                  │      Client Applications        │
                                  │   (Next.js 16 + React 19 UI)    │
                                  └──────────────┬──────────────────┘
                                                 │
                                                 │ HTTP / REST / JSON
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     FastAPI Backend Engine                                      │
│                                                                                                 │
│  ┌─────────────────────────┐  ┌───────────────────────────────┐  ┌───────────────────────────┐  │
│  │   Weather Guardrails    │  │ State-Aware Location Resolver │  │ JWT Auth & Guest Session  │  │
│  │  (Injection & Relevance)│  │   (Nominatim & Conversational)│  │       (Supabase Auth)     │  │
│  └───────────┬─────────────┘  └───────────────┬───────────────┘  └─────────────┬─────────────┘  │
│              │                                │                                │                │
│              ▼                                ▼                                ▼                │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                    Conversation Pipeline & Structured State Manager                       │  │
│  │         (Active Location · Origin · Destination · Cargo · Date/Time · Concern)            │  │
│  └───────┬────────────────────────────────────┬───────────────────────────────────┬──────────┘  │
│          │                                    │                                   │             │
│          ▼                                    ▼                                   ▼             │
│  ┌──────────────┐                     ┌──────────────┐                    ┌──────────────┐      │
│  │ Weather &    │                     │ Groq LLM     │                    │ Alert &      │      │
│  │ Route Engine │                     │ Reasoning    │                    │ Climate Svc  │      │
│  └───────┬──────┘                     └───────┬──────┘                    └───────┬──────┘      │
└──────────┼────────────────────────────────────┼───────────────────────────────────┼─────────────┘
           │                                    │                                   │
           ▼                                    ▼                                   ▼
┌───────────────────────┐           ┌───────────────────────┐           ┌───────────────────────┐
│  Open-Meteo Weather   │           │ Groq Cloud API        │           │ Supabase & Redis      │
│  & Climate API        │           │ (qwen/qwen3.8-27b)    │           │ (Postgres, State,     │
│  (Free, Keyless)      │           │                       │           │  Rate Limits, Cache)  │
└───────────────────────┘           └───────────────────────┘           └───────────────────────┘
```

---

## Key Functionalities & Features

### 1. Conversational AI Weather Assistant
- **Single-Turn & Multi-Turn Natural Language Reasoning**: Ask questions in plain English (*"Will it rain tomorrow in Chennai?"*, *"Do I need a jacket this evening?"*).
- **State-Aware Location Resolution**:
  - Dynamically updates active locations based on user context without relying on brittle keyword clobbering.
  - Correctly resolves origin/destination swaps, conversational corrections (*"I meant Bangalore, not Hyderabad"*), and contextual transitions (*"Actually, let's go to Pune instead"*).
- **Fast Conversational Memory**:
  - Maintains `StructuredConversationState` per user/conversation (active location, origin, destination, activity, cargo, date/time, weather concern, and previous recommendation).
  - Employs a sliding-window of recent messages combined with background rolling summaries.
  - Zero redundant LLM calls: Intent classification, state extraction, and natural-language synthesis happen in **exactly one LLM invocation** per query to maintain sub-second response latencies.
- **Two-Layer Guardrail Protection**:
  - Rejects prompt injection attempts (*"ignore previous instructions"*, *"reveal system prompt"*).
  - Distinguishes weather-relevant questions while maintaining conversational continuity when follow-ups omit explicit weather keywords (*"what about tomorrow?"*, *"is that safe?"*).

### 2. Travel Corridor Weather & Agricultural Cargo Recommendations
- **Dynamic Route Weather Inspection**:
  - Analyzes point-to-point road corridors (e.g., Chennai to Hyderabad) and samples intermediate waypoints.
  - Evaluates rainfall, crosswinds, visibility hazards, and road conditions along the travel corridor.
- **Interactive Route Itinerary Card (TravelCard)**:
  - UI component embedded directly in chat responses.
  - Displays departure time advisories, journey duration, overall corridor safety rating (Safe, Caution, Severe), and expandable waypoint breakdowns.
- **Agricultural Cargo Protection**:
  - Specialized risk models for agricultural commodities (e.g., harvested paddy/rice, raw cotton, perishable fruits, fertilizer, grain).
  - Generates actionable cargo safeguards (tarpaulin waterproofing, ventilation protocols, moisture prevention, and optimal departure delay windows).

### 3. Proactive Weather Alerts & Threshold Monitoring
- **Customizable Rule Engine**:
  - Set triggers based on meteorological criteria: Rain Probability (> %), Rainfall Volume (mm), Temperature Min/Max (°C), Wind Speed (km/h), or UV Index.
- **Automated Periodic Scans**:
  - Background task evaluates active user alerts against fresh Open-Meteo forecasts on a configurable interval (default: every 15 minutes).
- **In-Chat Alert Suggestions**:
  - When the assistant detects adverse or extreme weather during a conversation, it automatically offers a one-click in-chat alert creation action.
- **Persistent Storage**:
  - Alert definitions and delivery histories are persisted with Row-Level Security in Supabase PostgreSQL.

### 4. Climate Projections & Decadal Trends
- **Long-Term Climate Analytics**:
  - Powered by the Open-Meteo Climate API utilizing CMIP6 climate models.
  - Visualizes multi-decadal historical baselines compared to future warming trajectories and precipitation trends.
- **Interactive Recharts Dashboard**:
  - Dedicated `/climate` page rendering annual mean temperature changes, summer extreme shifts, and rainfall anomaly curves.

### 5. Location Intelligence & Geocoding
- **Hierarchical Location Resolution**:
  1. Explicit place name recognized in message query.
  2. Selected location in interactive Location Bar.
  3. Browser GPS coordinates (`latitude`, `longitude`) reverse-geocoded via OpenStreetMap Nominatim.
  4. Explicit clarification prompt (no hallucinated defaults or misleading IP estimates).
- **Recent Locations History**:
  - Redis-backed recent location tracking per user for instant re-queries.
  - 1-hour Redis TTL on geocoding lookups to comply with Nominatim usage policies and eliminate duplicate latency.

### 6. High-Performance Redis Cloud Caching & Resilience
WeatherGPT employs a centralized **Redis Cloud** caching architecture across all domains to eliminate redundant external API requests, slash response latencies to single-digit milliseconds, and guarantee system resilience against upstream outages:

- **Centralized Multi-Domain Caching (`TTLCache`)**:
  - Replaces single-instance in-memory storage with shared Redis Cloud, guaranteeing uniform cache state across horizontally scaled backend workers without authoritative local memory dicts.
  - **Weather Observation & Forecast Caching**: Weather data bundles (`weather_cache`) are indexed by geographic coordinates rounded to 2 decimal places (`~1.1 km` spatial resolution). Nearby queries share identical cache entries, drastically multiplying cache hit ratios.
  - **Geocoding & Place Search Cache**: Geocode results, reverse geocode lookups, and autocomplete queries (`location_cache`) are cached for 1 hour (`3600s`), complying with Nominatim usage guidelines while reducing geocoding latency to `<5ms`.
  - **Climate Analytics Cache**: Historical and CMIP6 climate model projections (`climate_cache`) are cached for 1 hour (`3600s`).
  - **Conversational Memory & State Cache**: Structured conversation state, recent sliding-window messages, and rolling summaries are cached in Redis with per-conversation keys (`conv:state:{id}`, `conv:recent:{id}`, `conv:summary:{id}`) for sub-millisecond context retrieval.

- **Stale-If-Error Fault Tolerance (7-Day Backup)**:
  - On every cache write, the system atomically stores both the active TTL-bounded key and a secondary `stale:*` mirror key with a **7-day retention period** (`86,400 × 7s`).
  - If upstream APIs (Open-Meteo or Nominatim) experience downtime, timeouts, or HTTP 429/5xx errors, WeatherGPT transparently serves the stale Redis cached data with warning logs, avoiding 502/503 errors and ensuring zero disruption for end users.

- **Request Coalescing (Thundering Herd Protection)**:
  - The `get_or_set` engine tracks in-flight asynchronous futures (`_inflight`).
  - If dozens of concurrent users request weather or route conditions for the same city simultaneously during a weather event, only **one single upstream network request** is executed; all other requests await and read from the single Redis write.

- **Centralized Atomic Rate Limiting (Redis Lua Scripting)**:
  - Distributed token/counter rate limiting (`RATE_LIMIT_CHAT`, `RATE_LIMIT_LOCATION`, `RATE_LIMIT_ALERTS`) executed via atomic Redis Lua scripts, preventing multi-worker race conditions.

- **Connection Pool Optimization & Fallbacks**:
  - Redis connection pool is explicitly capped (`max_connections=10`) with `socket_timeout=2.0s` and `socket_connect_timeout=2.0s` to prevent connection exhaustion on cloud serverless and shared Redis tiers.
  - Built-in graceful degradation: If Redis Cloud encounters transient network partitions, the system logs the issue and falls back gracefully rather than crashing.

- **Tiered Cache TTL Summary**:
  - **Current Weather Bundle**: `300s` (5 minutes)
  - **Hourly Forecasts**: `300s` (5 minutes)
  - **Daily Multi-Day Forecasts**: `600s` (10 minutes)
  - **Nominatim Geocoding & Reverse Geocoding**: `3600s` (1 hour)
  - **Climate Projections**: `3600s` (1 hour)
  - **Conversational State & Memory**: `3600s` (1 hour)
  - **Stale Fallback Data**: `604,800s` (7 days)

- **Circuit Breakers & Exponential Backoff**:
  - Upstream network calls are wrapped in sliding-window circuit breakers (`CIRCUIT_BREAKER_THRESHOLD=5`, `CIRCUIT_BREAKER_WINDOW=60s`) and retry loops (`RETRY_MAX_ATTEMPTS=3`) with exponential backoff.

### 7. Authentication & User Session Isolation
- **Supabase Authentication**:
  - Secure email/password login and registration.
  - Verified JWT validation with per-user conversation state and alert rule isolation.
- **Seamless Guest Mode**:
  - Unauthenticated users can immediately interact with the assistant with anonymous session tokens; history seamlessly persists during the active browser session.

---

## Tech Stack

| Layer | Technologies |
|---|---|
| **Frontend Framework** | [Next.js 16 (App Router)](https://nextjs.org), [React 19](https://react.dev) |
| **Styling & Design** | [Tailwind CSS v4](https://tailwindcss.com), Lucide Icons, Glassmorphism UI |
| **Frontend State & Fetching**| [Zustand](https://github.com/pmndrs/zustand), [TanStack React Query](https://tanstack.com/query) |
| **Data Visualization** | [Recharts](https://recharts.org) |
| **Backend Framework** | [FastAPI](https://fastapi.tiangolo.com) (Python 3.11+), [Uvicorn](https://www.uvicorn.org), [Pydantic v2](https://docs.pydantic.dev) |
| **AI / LLM Engine** | [Groq Cloud API](https://groq.com) (`qwen/qwen3.8-27b`) |
| **Weather & Climate Data** | [Open-Meteo Weather API](https://open-meteo.com), [Open-Meteo Climate API](https://open-meteo.com/en/docs/climate-api) (Free, no keys needed) |
| **Geocoding** | [Nominatim OpenStreetMap](https://nominatim.org) |
| **Cache & State Store** | [Redis Cloud](https://redis.io) (Connection-pooled) |
| **Database & Auth** | [Supabase](https://supabase.com) (PostgreSQL, Realtime, Row Level Security) |
| **Deployment** | [Render](https://render.com) (`render.yaml`), Vercel |

---

## Project Directory Structure

```
SAH/
├── README.md                      # Comprehensive Project Documentation
├── render.yaml                    # Multi-service Render deployment blueprint
├── .gitignore
│
├── backend/                       # FastAPI Application
│   ├── app/
│   │   ├── main.py                # App entrypoint, middleware, lifecycle handlers
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── chat.py        # POST /api/v1/chat (pipeline orchestration)
│   │   │       ├── weather.py     # GET /api/v1/weather (current, hourly, daily)
│   │   │       ├── location.py    # GET /api/v1/location (search, reverse, recent)
│   │   │       ├── alerts.py      # GET, POST, DELETE /api/v1/alerts
│   │   │       ├── climate.py     # GET /api/v1/climate
│   │   │       └── auth.py        # POST /api/v1/auth (login, register, session)
│   │   ├── services/
│   │   │   ├── conversation_service.py # StructuredConversationState & Redis memory
│   │   │   ├── llm_service.py          # Groq LLM client & prompt templates
│   │   │   ├── weather_service.py      # Open-Meteo API integration
│   │   │   ├── location_service.py     # Nominatim geocoding & route waypoints
│   │   │   ├── alert_service.py        # Threshold checker & Supabase sync
│   │   │   ├── climate_service.py      # Decadal climate analytics
│   │   │   └── recent_locations_service.py # Redis recent places list
│   │   ├── schemas/               # Pydantic validation schemas
│   │   ├── core/                  # Configuration, settings, security, Redis pool
│   │   └── utils/                 # Guardrails, HTTP helpers, circuit breakers
│   ├── tests/                     # Pytest unit & integration test suites
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md
│
└── frontend/                      # Next.js 16 Application
    ├── src/
    │   ├── app/
    │   │   ├── page.tsx           # Home landing & quick weather dashboard
    │   │   ├── chat/page.tsx      # Main WeatherGPT interactive chat interface
    │   │   ├── alerts/page.tsx    # Weather alert rule management dashboard
    │   │   ├── climate/page.tsx   # Climate trend graphs & projections
    │   │   ├── login/page.tsx     # Sign In page
    │   │   ├── register/page.tsx  # Sign Up page
    │   │   ├── layout.tsx         # Root layout with navigation & auth provider
    │   │   └── globals.css        # Tailwind CSS v4 styling rules
    │   ├── components/
    │   │   ├── navbar.tsx         # Top responsive navigation bar
    │   │   ├── location-bar.tsx   # Search & GPS coordinate selector
    │   │   ├── route-itinerary-card.tsx # Travel & cargo risk recommendation card
    │   │   ├── current-conditions.tsx # Atmospheric condition cards
    │   │   ├── hourly-strip.tsx   # 24-hour horizontal forecast timeline
    │   │   ├── forecast-list.tsx  # 7-day weather outlook
    │   │   └── weather-widget.tsx # Floating quick-check widget
    │   ├── lib/                   # API clients, auth helpers, utility functions
    │   ├── store/                 # Zustand state stores (location, chat, alerts)
    │   └── types/                 # TypeScript interfaces
    ├── package.json
    └── README.md
```

---

## API Endpoints Reference

### Chat & Assistant
- `POST /api/v1/chat` — Send natural language weather inquiries. Evaluates conversation state, fetches weather data, analyzes route risks if travelling, and synthesizes response.
- `GET /api/v1/chat/history` — Fetch recent messages and state for the current session.
- `DELETE /api/v1/chat/history` — Clear conversation history and reset memory state.

### Weather & Forecasts
- `GET /api/v1/weather/current` — Current temperature, humidity, wind, UV index, and weather code.
- `GET /api/v1/weather/forecast` — Multi-day daily forecast (high/low temp, precipitation chance).
- `GET /api/v1/weather/hourly` — 24-hour detailed conditions.
- `GET /api/v1/weather/route` — Multi-point travel corridor weather conditions.

### Location Intelligence
- `GET /api/v1/location/search?q={query}` — Search place names via Nominatim.
- `GET /api/v1/location/reverse?lat={lat}&lon={lon}` — Convert coordinates to city/region.
- `GET /api/v1/location/recent` — Retrieve user's recently searched locations.

### Alerts & Notifications
- `GET /api/v1/alerts` — List user's active alert rules.
- `POST /api/v1/alerts` — Create a new threshold-based alert rule.
- `DELETE /api/v1/alerts/{alert_id}` — Remove an alert rule.
- `POST /api/v1/alerts/check` — Force trigger an alert evaluation sweep.

### Climate Trends
- `GET /api/v1/climate?latitude={lat}&longitude={lon}` — Retrieve decadal climate projection data.

### System & Health
- `GET /healthz` — Basic liveness probe.
- `GET /api/v1/health` — Detailed health check (validating Redis, Supabase, Groq).

---

## Environment Variables

### Backend Configuration (`backend/.env`)

Copy `backend/.env.example` to `backend/.env` and configure:

```env
# ── Groq LLM ──────────────────────────────────────────────────────────
GROQ_API_KEY=gsk_your_groq_api_key
GROQ_MODEL=qwen/qwen3.8-27b
MAX_MESSAGE_LENGTH=1000
LLM_MAX_OUTPUT_TOKENS=400

# ── Supabase ──────────────────────────────────────────────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_JWT_SECRET=your-jwt-secret

# ── Redis Cloud ───────────────────────────────────────────────────────
REDIS_URL=redis://default:your-password@your-redis-host:port

# ── CORS ──────────────────────────────────────────────────────────────
CORS_ORIGINS=http://localhost:3000,https://your-frontend.vercel.app

# ── External Services ─────────────────────────────────────────────────
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1
OPEN_METEO_CLIMATE_URL=https://climate-api.open-meteo.com/v1
NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
NOMINATIM_USER_AGENT=WeatherGPT/2.0 (sih-weathergpt-backend)

# ── Conversational Memory ─────────────────────────────────────────────
RECENT_MESSAGE_LIMIT=10
SUMMARY_TRIGGER_THRESHOLD=12
MAX_CONTEXT_TOKENS=4000
CONVERSATION_CACHE_TTL=3600

# ── Cache TTLs (seconds) ──────────────────────────────────────────────
WEATHER_CACHE_TTL=300
FORECAST_CACHE_TTL=600
LOCATION_CACHE_TTL=3600
CLIMATE_CACHE_TTL=3600
```

### Frontend Configuration (`frontend/.env.local`)

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

---

## Getting Started

### Prerequisites
- **Python**: 3.11 or higher
- **Node.js**: 20.x or higher
- **Package Managers**: `pip` and `npm`
- **Accounts**:
  - [Groq Console](https://console.groq.com) (Free API Key)
  - [Supabase](https://supabase.com) (Free project)
  - [Redis Cloud](https://redis.com/try-free) (Free 30MB instance)

---

### Backend Setup

1. **Navigate to backend**:
   ```bash
   cd backend
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate    # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your GROQ_API_KEY, SUPABASE, and REDIS credentials
   ```

5. **Start the development server**:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
   Backend will be accessible at [http://localhost:8000](http://localhost:8000). Interactive Swagger docs available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

### Frontend Setup

1. **Navigate to frontend**:
   ```bash
   cd frontend
   ```

2. **Install npm dependencies**:
   ```bash
   npm install
   ```

3. **Configure environment**:
   Create a `.env.local` file:
   ```env
   NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
   ```

4. **Launch the development server**:
   ```bash
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) in your web browser.

---

## Running Tests

The backend includes a comprehensive suite of unit and integration tests covering conversational memory, state-aware location switching, weather guardrails, and corridor route analysis:

```bash
cd backend
pytest tests/ -v
```

To run a specific test suite:
```bash
# Test conversational state and multi-turn location switching
pytest tests/test_conversation_memory.py -v

# Test chat pipeline and guardrails
pytest tests/test_chat.py -v
```

---

## Deployment

The project is pre-configured for automated deployment on **Render** using the root [`render.yaml`](./render.yaml) blueprint:

- **Backend**: Python Web Service running `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Frontend**: Deployable on Vercel or as a static/Node web service on Render pointing to `frontend/`.

---

## License

This project is licensed under the MIT License — see the repository for details.
