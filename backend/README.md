# WeatherGPT — Phase 1 Backend

> **Conversational AI for Weather Forecasting, Alerts, and Climate Information**
> SIH Project · Phase 1

---

## Overview

WeatherGPT is a FastAPI backend that answers natural-language weather questions.

- **Input** — a plain-English question ("Will it rain tomorrow?"), optional browser GPS coordinates, or location.
- **Output** — a conversational answer backed by live weather data.
- **Guardrail** — non-weather questions are rejected before any external API is called.
- **LLM role** — Groq LLM *explains* normalized weather data; it never retrieves data directly.
- **Stack** — 100% free and open-source APIs (Open-Meteo + Nominatim OpenStreetMap + Browser Geolocation + Groq).

---

## Architecture

```
Client (Browser)
  │
  │  POST /api/v1/chat  {"message": "...", "latitude": 13.08, "longitude": 80.27}
  ▼
┌─────────────────────────────────────────────────────────────┐
│                          FastAPI                            │
│                                                             │
│  1. Request validation (Pydantic: message, lat, lon)        │
│  2. Weather Guardrail ──── non-weather → 403                │
│  3. Location Service  ──── Nominatim Geocoding / Browser GPS│
│  4. Weather Service   ──── Open-Meteo API (current/daily)   │
│  5. LLM Service       ──── Groq API                         │
│  6. Response validation                                     │
└─────────────────────────────────────────────────────────────┘
  │
  │  JSON response (answer + structured weather_data)
  ▼
Client
```

**Key design principle**: the backend is the source of truth for weather data.
The LLM only receives structured data and explains it — it never calls the API itself.

---

## Project Structure

```
weathergpt/
│
├── app/
│   ├── main.py                  ← FastAPI app, routers, logging, CORS
│   │
│   ├── api/
│   │   └── routes/
│   │       └── chat.py          ← POST /api/v1/chat (pipeline orchestration)
│   │
│   ├── services/
│   │   ├── weather_service.py   ← Open-Meteo API → WeatherData
│   │   ├── llm_service.py       ← WeatherData + question → answer string (Groq)
│   │   └── location_service.py  ← Nominatim geocoding & Browser GPS coordinates
│   │
│   ├── schemas/
│   │   └── chat.py              ← ChatRequest, ChatResponse, WeatherData
│   │
│   ├── core/
│   │   └── config.py            ← Pydantic Settings from .env
│   │
│   └── utils/
│       └── weather_guardrail.py ← is_weather_related(message) → bool
│
├── tests/
│   └── test_chat.py             ← unit + integration tests (pytest)
│
├── .env                         ← secrets (not committed)
├── .env.example                 ← template for .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Environment Variables

| Variable               | Required | Default                                  | Description                         |
|------------------------|----------|------------------------------------------|-------------------------------------|
| `GROQ_API_KEY`         | ✅       | —                                        | Groq API key (console.groq.com)     |
| `GROQ_MODEL`           | ❌       | `qwen/qwen3.8-27b`                       | Groq model name                     |
| `OPEN_METEO_BASE_URL`  | ❌       | `https://api.open-meteo.com/v1`          | Open-Meteo base URL (free, no key)  |
| `NOMINATIM_BASE_URL`   | ❌       | `https://nominatim.openstreetmap.org`    | OSM Nominatim base URL (free)       |
| `NOMINATIM_USER_AGENT` | ❌       | `WeatherGPT/1.0 (sih-weathergpt-backend)`| User-Agent for Nominatim OSM policy |
| `WEATHER_API_TIMEOUT`  | ❌       | `10`                                     | Weather API timeout (seconds)       |
| `LLM_TIMEOUT`          | ❌       | `30`                                     | LLM timeout (seconds)               |
| `GEO_TIMEOUT`          | ❌       | `5`                                      | Geolocation timeout (seconds)       |
| `DEBUG`                | ❌       | `false`                                  | Enable debug mode                   |

---

## Setup

### 1. Prerequisites

- Python 3.11+
- pip

### 2. Clone and install

```bash
cd weathergpt
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=qwen/qwen3.8-27b
```

Get a **free** Groq key at <https://console.groq.com>. Open-Meteo and Nominatim require **no API keys**.

### 4. Run the server

```bash
uvicorn app.main:app --reload
```

Server starts at: <http://localhost:8000>

---

## Location Priority Logic

The backend resolves location in this strict order:

1. **Explicit place in query or request** (e.g. "weather in Chennai", "forecast for Tokyo")  
   → Geocoded to coordinates via **Nominatim / OpenStreetMap**.
2. **Browser GPS Coordinates** (`latitude`, `longitude` passed in the JSON payload)  
   → Reverse geocoded via Nominatim for display name, coordinates sent to Open-Meteo.
3. **Neither available**  
   → Returns a polite prompt asking the user for their city name or to allow browser location access (no fake defaults, no IP guessing).

---

## API Endpoints

### `POST /api/v1/chat`

Send a natural-language weather question.

**Request body:**

```json
{
  "message": "Will it rain tomorrow?",
  "location": "Chennai",
  "latitude": 13.0827,
  "longitude": 80.2707
}
```

*All fields except `message` are optional.*

**Success response (`200`):**

```json
{
  "answer": "There is a 70% chance of rain in Chennai tomorrow. Carrying an umbrella would be wise.",
  "location": "Chennai",
  "weather_data": {
    "location": "Chennai",
    "temperature": 30.0,
    "feels_like": 34.0,
    "condition": "Partly cloudy",
    "humidity": 78,
    "wind_speed": 3.5,
    "rain_probability": 70.0,
    "rainfall": 1.2,
    "forecast_date": "2026-09-09"
  }
}
```

**Error responses:**

| Status | Meaning                              |
|--------|--------------------------------------|
| `403`  | Non-weather question (guardrail)     |
| `404`  | Location not found                   |
| `422`  | Validation error (empty message/coords)|
| `503`  | Open-Meteo or Nominatim timeout      |
| `502`  | Upstream API error                   |

### `GET /healthz`

Liveness check.

```json
{ "status": "ok", "version": "1.0.0" }
```

---

## Interactive Docs

Visit <http://localhost:8000/docs> for the Swagger UI with live examples.

---

## Example curl Requests

### Weather question with browser GPS coordinates

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the weather here?", "latitude": 13.0827, "longitude": 80.2707}'
```

### Weather question with place name in query

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Will it rain tomorrow in Hyderabad?"}'
```

### Non-weather question (rejected by guardrail)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me a joke."}'
```

Expected response:

```json
{
  "detail": "I can only help with weather-related questions."
}
```

---

## Guardrail Behaviour

The **weather guardrail** (`app/utils/weather_guardrail.py`) uses a two-layer check:

1. **Injection detection** — rejects messages containing phrases like  
   "ignore your instructions", "reveal your system prompt", "jailbreak", etc.
2. **Keyword scan** — accepts messages containing weather-related terms:  
   weather, rain, temperature, humidity, wind, sunny, cloudy, forecast, umbrella, outdoor, …

---

## Testing

Run all unit and integration tests:

```bash
pytest tests/ -v
```

All 47 tests execute against mocked services for predictable and fast CI runs.
