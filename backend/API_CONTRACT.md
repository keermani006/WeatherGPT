# WeatherGPT Phase 1 API Contract

**Base URL**: `http://localhost:8000` (configurable via `NEXT_PUBLIC_API_URL` or `VITE_API_URL`)  
**Swagger Interactive Documentation**: `http://localhost:8000/docs`  
**OpenAPI Specification**: `http://localhost:8000/openapi.json`  

---

## 1. Overview

This document defines the complete API contract for the WeatherGPT Phase 1 backend. All endpoints are fully implemented, validated with 91 automated unit/integration tests, and live for frontend integration.

### Technology Stack & Architecture
- **Backend Framework**: FastAPI (Python 3.13) with asynchronous request pipelines.
- **Authentication**: Supabase Auth JWT verification (HS256) on protected routes.
- **Caching**: Multi-tiered in-memory TTL caching with single-flight request coalescing.
- **Resilience**: Upstream retry with exponential backoff & sliding-window Circuit Breakers.
- **Rate Limiting**: IP-based rate limiting via SlowAPI (in-memory sliding window).
- **Security Middleware**: Automatic `X-Request-ID` tracing & OWASP security headers.
- **Weather Provider**: [Open-Meteo](https://open-meteo.com) (free tier, no API key required).
- **Climate Provider**: [Open-Meteo Climate API](https://climate-api.open-meteo.com) (CMIP6 climate models).
- **Geocoding & Reverse Geocoding**: [Nominatim / OpenStreetMap](https://nominatim.openstreetmap.org).
- **User Coordinates**: Browser Geolocation API (`navigator.geolocation.getCurrentPosition`).
- **AI / LLM Engine**: [Groq API](https://console.groq.com) (`qwen/qwen3.8-27b`).
- **Alert Persistence**: [Supabase](https://supabase.com) (PostgreSQL) with Row-Level Security (RLS) & in-memory fallback.
- **Alert Scheduler**: Lightweight async lifespan background scheduler with overlap prevention.
- **CORS**: Configured via `CORS_ORIGINS`.

---

## 2. Table of Endpoints

| Method | Endpoint | Auth Required | Description |
|---|---|---|---|
| `GET` | [`/health`](#1-get-health) | No | Service liveness check |
| `GET` | [`/health/ready`](#1b-get-healthready) | No | Service readiness probe (checks Supabase connectivity) |
| `GET` | [`/api/v1/weather/current`](#2-get-apiv1weathercurrent) | No | Current real-time weather conditions (cached 5m) |
| `GET` | [`/api/v1/weather/forecast`](#3-get-apiv1weatherforecast) | No | Multi-day daily forecast (1 to 16 days, cached 10m) |
| `GET` | [`/api/v1/weather/hourly`](#4-get-apiv1weatherhourly) | No | 24-hour hourly forecast for a date (cached 5m) |
| `POST` | [`/api/v1/chat`](#5-post-apiv1chat) | No | Natural language weather assistant (rate-limited 20/min) |
| `GET` | [`/api/v1/location/search`](#6-get-apiv1locationsearch) | No | Search places & autocomplete to coordinates (cached 1h, 30/min) |
| `POST` | [`/api/v1/alerts`](#7-post-apiv1alerts) | **Yes (Bearer JWT)** | Create a user-owned weather threshold alert (20/min) |
| `GET` | [`/api/v1/alerts`](#8-get-apiv1alerts) | **Yes (Bearer JWT)** | List authenticated user's registered alerts with trigger status |
| `DELETE` | [`/api/v1/alerts/{alert_id}`](#9-delete-apiv1alertsalert_id) | **Yes (Bearer JWT)** | Delete a user-owned alert by ID |
| `POST` | [`/api/v1/alerts/evaluate`](#10-post-apiv1alertsevaluate) | No | Evaluate all active alerts immediately against real weather |
| `GET` | [`/api/v1/climate`](#11-get-apiv1climate) | No | CMIP6 climate model projections, summaries & trends (cached 1h) |

---

## 3. Detailed Endpoint Specifications

### 1. `GET /health`

Lightweight liveness and readiness probe for load balancers and deployment verification.

#### Query Parameters
None.

#### Success Response
- **Status**: `200 OK`
```json
{
  "status": "healthy"
}
```

#### Frontend Notes
- Use for pre-flight connection checks or server status indicators in UI.

---

### 2. `GET /api/v1/weather/current`

Fetches current weather observations for given coordinates. Location name is automatically reverse-geocoded via Nominatim or can be overridden.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `latitude` | `float` | Yes | - | Latitude coordinate (`-90.0` to `90.0`). |
| `longitude` | `float` | Yes | - | Longitude coordinate (`-180.0` to `180.0`). |
| `location_name` | `string` | No | `null` | Optional pre-resolved location name (skips reverse geocoding). |

#### Success Response
- **Status**: `200 OK`
```json
{
  "location": {
    "name": "Chennai Corporation",
    "latitude": 13.0827,
    "longitude": 80.2707
  },
  "current": {
    "temperature": 35.2,
    "feels_like": 39.4,
    "humidity": 43,
    "wind_speed": 0.9,
    "precipitation": 0.0,
    "rain_probability": 32,
    "condition": "Clear sky"
  },
  "updated_at": "2026-09-08T15:30"
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `422 Unprocessable Entity` | - | Missing or out-of-range coordinates. |
| `502 Bad Gateway` | `WEATHER_SERVICE_ERROR` | Open-Meteo returned HTTP error. |
| `503 Service Unavailable` | `WEATHER_SERVICE_TIMEOUT` | Open-Meteo timed out (>10s). |

#### Frontend Notes
- Coordinates obtained via `navigator.geolocation.getCurrentPosition` can be passed directly.
- Caching: Cache client-side for 5–10 minutes.

---

### 3. `GET /api/v1/weather/forecast`

Retrieves multi-day daily weather forecast (temperature extremes, condition, precipitation probability, total precipitation, max wind speed).

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `latitude` | `float` | Yes | - | Latitude coordinate (`-90.0` to `90.0`). |
| `longitude` | `float` | Yes | - | Longitude coordinate (`-180.0` to `180.0`). |
| `days` | `integer` | No | `7` | Forecast horizon in days (`1` to `16`). |
| `location_name` | `string` | No | `null` | Optional display name for the location. |

#### Success Response
- **Status**: `200 OK`
```json
{
  "location": {
    "name": "Chennai Corporation",
    "latitude": 13.0827,
    "longitude": 80.2707
  },
  "forecast": [
    {
      "date": "2026-09-08",
      "temperature_max": 35.8,
      "temperature_min": 25.2,
      "rain_probability": 84,
      "precipitation": 3.7,
      "wind_speed": 14.7,
      "condition": "Slight rain showers"
    },
    {
      "date": "2026-09-09",
      "temperature_max": 33.7,
      "temperature_min": 25.2,
      "rain_probability": 39,
      "precipitation": 2.9,
      "wind_speed": 13.4,
      "condition": "Slight rain showers"
    }
  ]
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_FORECAST_DAYS` | `days` outside valid range (1–16). |
| `422 Unprocessable Entity` | - | Missing or invalid coordinates. |
| `502 Bad Gateway` | `WEATHER_SERVICE_ERROR` | Upstream service error. |
| `503 Service Unavailable` | `WEATHER_SERVICE_TIMEOUT` | Upstream timeout. |

#### Frontend Notes
- Ideal for 7-day or 14-day weather cards and charts.
- Recommended UI display: High/low temperature pills, rain probability badge (`84%`), weather condition icon.

---

### 4. `GET /api/v1/weather/hourly`

Provides hour-by-hour forecast (temperature, precipitation probability, precipitation volume, wind speed) for a 24-hour period.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `latitude` | `float` | Yes | - | Latitude coordinate (`-90.0` to `90.0`). |
| `longitude` | `float` | Yes | - | Longitude coordinate (`-180.0` to `180.0`). |
| `date` | `string` | Yes | - | Target date in `YYYY-MM-DD` format (within past 2 days to next 15 days). |

#### Success Response
- **Status**: `200 OK`
```json
{
  "location": {
    "name": "Chennai Corporation",
    "latitude": 13.0827,
    "longitude": 80.2707
  },
  "date": "2026-09-08",
  "hourly": [
    {
      "time": "00:00",
      "temperature": 27.7,
      "rain_probability": 41,
      "precipitation": 0.8,
      "wind_speed": 13.0
    },
    {
      "time": "01:00",
      "temperature": 25.2,
      "rain_probability": 70,
      "precipitation": 2.1,
      "wind_speed": 11.9
    },
    {
      "time": "12:00",
      "temperature": 35.3,
      "rain_probability": 6,
      "precipitation": 0.1,
      "wind_speed": 10.5
    }
  ]
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_DATE_FORMAT` | Date format is not `YYYY-MM-DD`. |
| `400 Bad Request` | `DATE_OUT_OF_RANGE` | Date outside allowed window. |
| `422 Unprocessable Entity` | - | Missing required parameters. |

#### Frontend Notes
- Useful for interactive hourly sliders, area charts (temperature gradient & rain probability bars).

---

### 5. `POST /api/v1/chat`

Natural language conversational interface powered by Groq LLM with strict weather-only guardrails. Location is intelligently resolved using a strict priority ladder:
1. **Explicit place in message / request** (e.g. "What's the weather in Mumbai?" overrides GPS).
2. **Browser GPS coordinates** (when question is local like "Will it rain today?").
3. **Clarification prompt** if no location is detected and no GPS is supplied.

#### Request Body (JSON)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `message` | `string` | Yes | - | User question (1 to 500 characters). |
| `latitude` | `float` | No | `null` | User GPS latitude from browser geolocation. |
| `longitude` | `float` | No | `null` | User GPS longitude from browser geolocation. |
| `location` | `string` | No | `null` | Explicit location name override. |

```json
{
  "message": "Will I need an umbrella today?",
  "latitude": 13.0827,
  "longitude": 80.2707
}
```

#### Success Response
- **Status**: `200 OK`
```json
{
  "answer": "Yes, you should definitely take an umbrella. Rain showers are expected today in Chennai with an 84% chance of precipitation.",
  "location": "Chennai Corporation",
  "weather_data": {
    "location": "Chennai Corporation",
    "temperature": 35.2,
    "feels_like": 39.4,
    "condition": "Slight rain showers",
    "humidity": 43,
    "wind_speed": 0.9,
    "rain_probability": 84,
    "rainfall": 3.7,
    "forecast_date": "2026-09-08"
  }
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `LOCATION_REQUIRED` | Missing location and coordinates. Prompt frontend to request city or GPS. |
| `403 Forbidden` | `WEATHER_GUARDRAIL_TRIGGERED` | Query is non-weather (e.g. coding, politics, jokes) or prompt injection. |
| `404 Not Found` | `LOCATION_NOT_FOUND` | Named location could not be geocoded by Nominatim. |
| `502 Bad Gateway` | `WEATHER_API_ERROR` | Open-Meteo failure. |
| `503 Service Unavailable` | `WEATHER_API_TIMEOUT` | Weather service timed out. |

#### Frontend Notes
- Check `403` status to display a friendly guardrail message: `"I am WeatherGPT, your weather assistant. I can only answer questions related to weather, climate, and meteorological conditions."`
- The returned `weather_data` object can be rendered as a rich widget alongside the LLM chat bubble.

---

### 6. `GET /api/v1/location/search`

Autocompletes location names and returns geographic coordinates with country identifiers via Nominatim.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `q` (or `query`) | `string` | Yes | - | Search query text (e.g. `"Hyderabad"`, `"Tokyo"`). |
| `limit` | `integer` | No | `5` | Maximum results to return (1 to 10). |

#### Success Response
- **Status**: `200 OK`
```json
{
  "results": [
    {
      "name": "Hyderabad",
      "country": "India",
      "latitude": 17.360589,
      "longitude": 78.4740613
    },
    {
      "name": "Hyderabad",
      "country": "Pakistan",
      "latitude": 25.394449,
      "longitude": 68.373056
    }
  ]
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_QUERY` | Search query is whitespace only. |
| `422 Unprocessable Entity` | `MISSING_QUERY` | Query parameter `q` is omitted or empty. |
| `502 Bad Gateway` | `GEOCODING_SERVICE_UNAVAILABLE` | Nominatim upstream error or timeout. |

#### Frontend Notes
- Implement debouncing (300ms–500ms) on search input fields to respect OpenStreetMap Nominatim rate limits (1 req/sec).

---

### 7. `POST /api/v1/alerts`

Registers a meteorological threshold alert. Persisted in Supabase PostgreSQL (or in-memory store if DB credentials are not configured).

#### Request Body (JSON)

| Field | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `latitude` | `float` | Yes | `-90.0` to `90.0` | Alert coordinate latitude. |
| `longitude` | `float` | Yes | `-180.0` to `180.0` | Alert coordinate longitude. |
| `condition` | `string` | Yes | `"rain_probability"`, `"temperature"`, `"wind_speed"`, `"precipitation"` | Monitored meteorological metric. |
| `threshold` | `float` | Yes | Number (e.g. `0-100` for rain) | Trigger value threshold. |
| `location_name` | `string` | No | `null` | User-friendly location label. |

```json
{
  "latitude": 13.0827,
  "longitude": 80.2707,
  "condition": "rain_probability",
  "threshold": 70.0,
  "location_name": "Chennai"
}
```

#### Success Response
- **Status**: `201 Created`
```json
{
  "id": "alert_77d83f67",
  "latitude": 13.0827,
  "longitude": 80.2707,
  "condition": "rain_probability",
  "threshold": 70.0,
  "active": true,
  "location_name": "Chennai",
  "triggered": false,
  "current_value": null,
  "evaluated_at": null,
  "last_triggered_at": null,
  "created_at": "2026-09-08T10:14:19.241885+00:00"
}
```

#### Condition Semantics & Evaluation Rules

| Condition | Unit | Comparison | Trigger Condition |
|---|---|---|---|
| `rain_probability` | `%` | `>=` | Triggered when `current.rain_probability >= threshold` |
| `temperature` | `°C` | `>=` | Triggered when `current.temperature >= threshold` |
| `wind_speed` | `km/h` | `>=` | Triggered when `current.wind_speed >= threshold` |
| `precipitation` | `mm` | `>=` | Triggered when `current.precipitation >= threshold` |

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_THRESHOLD` | Threshold outside logical range (e.g. rain prob > 100% or < 0%). |
| `422 Unprocessable Entity` | - | Missing fields or invalid condition enum. |

---

### 8. `GET /api/v1/alerts`

Lists all configured weather alerts including live triggered status, last evaluated timestamp, and observed values.

#### Query Parameters
None.

#### Success Response
- **Status**: `200 OK`
```json
{
  "alerts": [
    {
      "id": "alert_77d83f67",
      "latitude": 13.0827,
      "longitude": 80.2707,
      "condition": "rain_probability",
      "threshold": 70.0,
      "active": true,
      "location_name": "Chennai",
      "triggered": true,
      "current_value": 84.0,
      "evaluated_at": "2026-09-08T15:30:00+00:00",
      "last_triggered_at": "2026-09-08T15:30:00+00:00",
      "created_at": "2026-09-08T10:14:19.241885+00:00"
    }
  ]
}
```

#### Frontend Display Recommendation
When `triggered === true`, display an alert banner:
```text
⚠️ Rain Alert Triggered
Location: Chennai
Current rain probability: 84% (Threshold: 70%)
Evaluated at: 15:30 UTC
```

---

### 9. `DELETE /api/v1/alerts/{alert_id}`

Deletes an active alert by its identifier.

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `alert_id` | `string` | Yes | Unique alert identifier (e.g. `"alert_77d83f67"`). |

#### Success Response
- **Status**: `200 OK`
```json
{
  "message": "Alert deleted successfully.",
  "id": "alert_77d83f67"
}
```

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `404 Not Found` | `ALERT_NOT_FOUND` | Alert with given ID does not exist. |

---

### 10. `POST /api/v1/alerts/evaluate`

Immediately evaluates all active alerts against real-time Open-Meteo weather data and updates their `triggered` and `current_value` state.

#### Query Parameters / Body
None.

#### Success Response
- **Status**: `200 OK`
```json
{
  "evaluated": 2,
  "triggered": 1,
  "results": [
    {
      "alert_id": "alert_77d83f67",
      "triggered": true,
      "current_value": 84.0,
      "condition": "rain_probability",
      "threshold": 70.0,
      "evaluated_at": "2026-09-08T15:30:00+00:00",
      "error": null
    },
    {
      "alert_id": "alert_8c68fd9c",
      "triggered": false,
      "current_value": 28.5,
      "condition": "temperature",
      "threshold": 35.0,
      "evaluated_at": "2026-09-08T15:30:00+00:00",
      "error": null
    }
  ]
}
```

#### Automatic Evaluation & Scheduler Notes
- **Background Scheduler**: Evaluates active alerts periodically in the background via FastAPI lifespan task.
- **Interval**: Configurable via `ALERT_CHECK_INTERVAL_MINUTES=15` in `.env`.
- **Anti-Spam & State Transitions**:
  - Transition from `triggered=false` to `triggered=true` sets `last_triggered_at = now`.
  - Consecutive evaluation cycles where the condition remains above the threshold **do not** generate duplicate trigger events (`last_triggered_at` is preserved).
  - If weather condition drops below threshold, `triggered` reverts to `false`. When it crosses threshold again in the future, it re-triggers with a new `last_triggered_at`.
- **Persistence Limitation**: If Supabase credentials are configured, alerts and triggered statuses persist across server restarts. If running in in-memory fallback mode, alerts are stored in process memory and reset when the server restarts.

---

### 11. `GET /api/v1/climate`

Retrieves CMIP6 climate model projections (temperature, precipitation, humidity, wind) powered by the **Open-Meteo Climate API**, including calculated summaries, trends, and required attribution.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `latitude` | `float` | Yes | - | Latitude coordinate (`-90.0` to `90.0`). |
| `longitude` | `float` | Yes | - | Longitude coordinate (`-180.0` to `180.0`). |
| `start_date` | `string` | No | `2025-01-01` | Start date in `YYYY-MM-DD` format. |
| `end_date` | `string` | No | `2025-01-30` | End date in `YYYY-MM-DD` format (max span: 10 years). |
| `model` | `string` | No | `CMCC_CM2_VHR4` | CMIP6 model (`CMCC_CM2_VHR4`, `EC_Earth3P_HR`, `MPI_ESM1_2_XR`). |

#### Success Response
- **Status**: `200 OK`
```json
{
  "location": {
    "latitude": 13.0827,
    "longitude": 80.2707,
    "name": "Chennai Corporation"
  },
  "period": {
    "start": "2025-01-01",
    "end": "2025-01-30"
  },
  "model": "CMCC_CM2_VHR4",
  "daily": [
    {
      "date": "2025-01-01",
      "temperature_mean": 25.0,
      "temperature_max": 28.2,
      "temperature_min": 22.0,
      "precipitation": 3.47,
      "humidity": 82.0,
      "wind_speed": 17.2
    },
    {
      "date": "2025-01-02",
      "temperature_mean": 24.8,
      "temperature_max": 28.7,
      "temperature_min": 22.0,
      "precipitation": 4.21,
      "humidity": 76.0,
      "wind_speed": 10.7
    }
  ],
  "summary": {
    "average_temperature": 24.5,
    "total_precipitation": 40.6,
    "average_humidity": 72.4,
    "average_wind_speed": 14.1
  },
  "trend": {
    "temperature": "stable",
    "precipitation": "decreasing"
  },
  "data_source": {
    "provider": "Open-Meteo",
    "dataset": "CMIP6",
    "attribution": "Open-Meteo / CMIP6"
  }
}
```

#### Attribution Requirement
The frontend **must** display dataset attribution (e.g. `"Data: Open-Meteo / CMIP6"`) when presenting climate projections, adhering to Open-Meteo terms of use.

#### Error Responses

| Status Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_DATE_FORMAT` | `start_date` or `end_date` is not in `YYYY-MM-DD` format. |
| `400 Bad Request` | `INVALID_DATE_RANGE` | `start_date` is chronologically after `end_date`. |
| `400 Bad Request` | `DATE_RANGE_TOO_LARGE` | Date span exceeds 10 years (3650 days). |
| `422 Unprocessable Entity` | - | Missing or out-of-range coordinates. |
| `502 Bad Gateway` | `CLIMATE_SERVICE_ERROR` | Open-Meteo Climate API returned an upstream error. |
| `503 Service Unavailable` | `CLIMATE_SERVICE_TIMEOUT` | Open-Meteo Climate API timed out. |

---

## 4. Standard Error Envelope

When errors occur, the backend returns standard HTTP status codes accompanied by a structured JSON payload:

```json
{
  "detail": {
    "error": {
      "code": "ERROR_CODE_STRING",
      "message": "Human-readable description of what went wrong."
    }
  }
}
```

### Standard Error Codes

| Code | HTTP Status | Meaning |
|---|---|---|
| `LOCATION_REQUIRED` | `400` | The query requires a location and neither coordinates nor place name were provided. |
| `INVALID_QUERY` | `400` | The search string was empty or contained only whitespace. |
| `WEATHER_GUARDRAIL_TRIGGERED` | `403` | Message rejected because it is not weather-related. |
| `LOCATION_NOT_FOUND` | `404` | Named place could not be resolved to coordinates. |
| `ALERT_NOT_FOUND` | `404` | No alert with that ID exists. |
| `INVALID_DATE_FORMAT` | `400` | Date query parameter is not in YYYY-MM-DD format. |
| `INVALID_DATE_RANGE` | `400` | Start date is chronologically after end date. |
| `DATE_RANGE_TOO_LARGE` | `400` | Climate date span exceeds 10 years (3650 days). |
| `GEOCODING_SERVICE_UNAVAILABLE` | `502` | Nominatim service failed or was unreachable. |
| `WEATHER_SERVICE_ERROR` | `502` | Open-Meteo weather returned an upstream error. |
| `WEATHER_SERVICE_TIMEOUT` | `503` | Open-Meteo weather did not respond within timeout limits. |
| `CLIMATE_SERVICE_ERROR` | `502` | Open-Meteo Climate API returned an upstream error. |
| `CLIMATE_SERVICE_TIMEOUT` | `503` | Open-Meteo Climate API did not respond within timeout limits. |

---

## 5. CORS Configuration

- **Development**: Allowed origins default to local frontends (`http://localhost:3000`, `http://localhost:5173`, `http://localhost:8080`) or `*`.
- **Production**: Configure the `CORS_ORIGINS` environment variable in `.env` as a comma-separated list restricted to your deployed frontend domain (e.g. `https://weathergpt.yourdomain.com`).

---

## 6. Frontend Integration Guide (Quick Reference)

### Recommended Next.js / React Client Fetcher
```typescript
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function askWeatherAssistant(message: string, coords?: { latitude: number; longitude: number }) {
  const response = await fetch(`${API_BASE_URL}/api/v1/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      latitude: coords?.latitude ?? null,
      longitude: coords?.longitude ?? null,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(errorData.detail?.error?.message || 'Chat request failed');
  }

  return response.json();
}
```
