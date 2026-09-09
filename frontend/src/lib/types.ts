/* ──────────────────────────────────────────────
 * WeatherGPT API Types
 * Matches real FastAPI backend contract exactly:
 * D:\Projects\SIH\backend\API_CONTRACT.md
 * ────────────────────────────────────────────── */

// ── Shared ────────────────────────────────────

export interface ApiErrorEnvelope {
  detail: {
    error: {
      code: string;
      message: string;
    };
  };
}

// ── 1. GET /health ────────────────────────────

export interface HealthResponse {
  status: string;
}

// ── 2. GET /api/v1/weather/current ────────────
// Params: latitude, longitude, location_name?

export interface CurrentWeatherResponse {
  location: {
    name: string;
    latitude: number;
    longitude: number;
  };
  current: {
    temperature: number;
    feels_like: number;
    humidity: number;
    wind_speed: number;
    precipitation: number;
    rain_probability: number;
    condition: string;
  };
  updated_at: string;
}

// ── 3. GET /api/v1/weather/forecast ───────────
// Params: latitude, longitude, days?, location_name?

export interface ForecastDayEntry {
  date: string;
  temperature_max: number;
  temperature_min: number;
  rain_probability: number;
  precipitation: number;
  wind_speed: number;
  condition: string;
}

export interface ForecastResponse {
  location: {
    name: string;
    latitude: number;
    longitude: number;
  };
  forecast: ForecastDayEntry[];
}

// ── 4. GET /api/v1/weather/hourly ─────────────
// Params: latitude, longitude, date (YYYY-MM-DD)

export interface HourlyEntry {
  time: string; // "HH:MM" format
  temperature: number;
  rain_probability: number;
  precipitation: number;
  wind_speed: number;
}

export interface HourlyWeatherResponse {
  location: {
    name: string;
    latitude: number;
    longitude: number;
  };
  date: string;
  hourly: HourlyEntry[];
}

// ── 5. POST /api/v1/chat ──────────────────────
// Body: message, latitude?, longitude?, location?
// Response: answer (not reply!)

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface WeatherData {
  location?: string;
  temperature?: number;
  feels_like?: number;
  condition?: string;
  humidity?: number;
  wind_speed?: number; // number from real backend
  rain_probability?: number;
  rainfall?: number;
  forecast_date?: string;
}

export interface ChatResponse {
  answer: string; // real backend uses "answer" not "reply"
  location: string;
  weather_data?: WeatherData;
}

// ── 6 & 7. POST/GET /api/v1/alerts ───────────
// Real backend conditions (not _above/_below suffixes):

export type AlertCondition =
  | "rain_probability"
  | "temperature"
  | "wind_speed"
  | "precipitation";

export interface Alert {
  id: string;
  latitude: number;   // real backend uses latitude (not lat)
  longitude: number;  // real backend uses longitude (not lng)
  condition: string;
  threshold: number;
  active: boolean;
  location_name: string | null;
  triggered: boolean;
  current_value: number | null;
  evaluated_at: string | null;
  last_triggered_at: string | null;
  created_at: string;
}

export type CreateAlertResponse = Alert;

export interface AlertsListResponse {
  alerts: Alert[];
}

// ── 8. DELETE /api/v1/alerts/{alert_id} ───────
// Response: 200 { message, id } (not 204)

export interface DeleteAlertResponse {
  message: string;
  id: string;
}

// ── 9. POST /api/v1/alerts/evaluate ──────────

export interface EvaluateAlertsResponse {
  evaluated: number;
  triggered: number;
  results: Array<{
    alert_id: string;
    triggered: boolean;
    current_value: number;
    condition: string;
    threshold: number;
    evaluated_at: string;
    error: string | null;
  }>;
}

// ── 10. GET /api/v1/climate ───────────────────
// Real CMIP6 model identifiers from API contract:

export type ClimateModel =
  | "CMCC_CM2_VHR4"
  | "EC_Earth3P_HR"
  | "MPI_ESM1_2_XR";

export interface ClimateDailyEntry {
  date: string;
  temperature_mean: number;
  temperature_max: number;
  temperature_min: number;
  precipitation: number;
  humidity: number;
  wind_speed: number;
}

export interface ClimateResponse {
  location: {
    latitude: number;
    longitude: number;
    name: string;
  };
  period: { start: string; end: string };
  model: string;
  daily: ClimateDailyEntry[];
  summary: {
    average_temperature: number;   // not avg_temperature
    total_precipitation: number;
    average_humidity: number;      // not avg_humidity
    average_wind_speed: number;    // not avg_wind_speed
  };
  trend: { temperature: string; precipitation: string };
  data_source: {
    provider: string;
    dataset: string;
    attribution: string;
  };
}

// ── 11. GET /api/v1/location/search ──────────
// Param: q (not query), limit?
// Response: results[].latitude / results[].longitude (not lat/lng)

export interface LocationResult {
  name: string;
  country: string;
  latitude: number;
  longitude: number;
}

export interface LocationSearchResponse {
  results: LocationResult[];
}
