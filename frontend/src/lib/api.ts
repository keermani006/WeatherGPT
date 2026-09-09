/* ──────────────────────────────────────────────
 * Typed API client — WeatherGPT FastAPI backend
 * Base URL: NEXT_PUBLIC_API_URL (default http://localhost:8000)
 * Contract: d:\Projects\SIH\backend\API_CONTRACT.md
 * ────────────────────────────────────────────── */

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// ── Error class ──────────────────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Auth Token Management ───────────────────────────────────────────────────
let memoryToken: string | null = null;

export function getAuthToken(): string | null {
  if (typeof window !== "undefined") {
    try {
      return localStorage.getItem("weathergpt_token") || memoryToken;
    } catch {
      return memoryToken;
    }
  }
  return memoryToken;
}

export function setAuthToken(token: string | null) {
  memoryToken = token;
  if (typeof window !== "undefined") {
    try {
      if (token) {
        localStorage.setItem("weathergpt_token", token);
      } else {
        localStorage.removeItem("weathergpt_token");
      }
    } catch {
      // ignore storage quota / sandbox errors
    }
  }
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) || {}),
  };

  if (token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (res.status === 204) return undefined as T;

  const json = await res.json().catch(() => ({}));

  if (!res.ok) {
    const err = json?.detail?.error || (typeof json?.detail === "object" && json?.detail?.code ? json.detail : null);
    const code =
      err?.code ??
      (res.status === 403
        ? "WEATHER_GUARDRAIL_TRIGGERED"
        : res.status === 404
        ? "LOCATION_NOT_FOUND"
        : res.status === 401
        ? "UNAUTHORIZED"
        : "UNKNOWN_ERROR");
    const message =
      err?.message ??
      (typeof json?.detail === "string"
        ? json.detail
        : "An unexpected error occurred.");
    throw new ApiError(code, message, res.status);
  }


  return json as T;
}


// ── Helper: build query string ───────────────────────────────────────────────
function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

// ── 1. Health ────────────────────────────────────────────────────────────────
export function getHealth() {
  return request<{ status: string }>("/health");
}

// ── 2. Current weather ───────────────────────────────────────────────────────
// Backend param: latitude / longitude (not lat/lng)
export function getCurrentWeather(params: {
  lat: number;
  lng: number;
  location_name?: string;
}) {
  return request<{
    location: { name: string; latitude: number; longitude: number };
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
  }>(
    `/api/v1/weather/current${qs({
      latitude: params.lat,
      longitude: params.lng,
      location_name: params.location_name,
    })}`
  );
}

// ── 3. Forecast ──────────────────────────────────────────────────────────────
export function getForecast(params: {
  lat: number;
  lng: number;
  days?: number;
  location_name?: string;
}) {
  return request<{
    location: { name: string; latitude: number; longitude: number };
    forecast: Array<{
      date: string;
      temperature_max: number;
      temperature_min: number;
      rain_probability: number;
      precipitation: number;
      wind_speed: number;
      condition: string;
    }>;
  }>(
    `/api/v1/weather/forecast${qs({
      latitude: params.lat,
      longitude: params.lng,
      days: params.days ?? 7,
      location_name: params.location_name,
    })}`
  );
}

// ── 4. Hourly ────────────────────────────────────────────────────────────────
export function getHourlyWeather(params: {
  lat: number;
  lng: number;
  date: string;
}) {
  return request<{
    location: { name: string; latitude: number; longitude: number };
    date: string;
    hourly: Array<{
      time: string;
      temperature: number;
      rain_probability: number;
      precipitation: number;
      wind_speed: number;
    }>;
  }>(
    `/api/v1/weather/hourly${qs({
      latitude: params.lat,
      longitude: params.lng,
      date: params.date,
    })}`
  );
}

// ── 5. Chat ──────────────────────────────────────────────────────────────────
// Backend uses: latitude/longitude/location (not lat/lng/location_name)
// Response: answer (not reply), weather_data with rainfall field
export function sendChatMessage(params: {
  message: string;
  lat?: number;
  lng?: number;
  location_name?: string;
  history?: { role: string; content: string }[];
}) {
  const isPlaceholder =
    !params.location_name ||
    ["current location", "current", "my location", "here", "device location", "gps", "unknown"].includes(
      params.location_name.toLowerCase().trim()
    );

  return request<{
    answer: string;
    location: string;
    weather_data?: {
      location: string;
      temperature: number;
      feels_like: number;
      condition: string;
      humidity: number;
      wind_speed: number;
      rain_probability: number;
      rainfall?: number;
      forecast_date?: string;
    };
  }>("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify({
      message: params.message,
      latitude: params.lat ?? null,
      longitude: params.lng ?? null,
      location: isPlaceholder ? null : params.location_name,
    }),
  });

}

// ── 6. Location search ───────────────────────────────────────────────────────
// Backend param: q (not query)
// Response: results[].latitude / results[].longitude (not lat/lng)
export function searchLocations(params: { query: string; limit?: number }) {
  return request<{
    results: Array<{
      name: string;
      country: string;
      latitude: number;
      longitude: number;
    }>;
  }>(
    `/api/v1/location/search${qs({
      q: params.query,
      limit: params.limit ?? 5,
    })}`
  );
}

// ── 7 & 8. Alerts ────────────────────────────────────────────────────────────
// Backend condition values: rain_probability | temperature | wind_speed | precipitation
export function getAlerts() {
  return request<{
    alerts: Array<{
      id: string;
      latitude: number;
      longitude: number;
      condition: string;
      threshold: number;
      active: boolean;
      location_name: string | null;
      triggered: boolean;
      current_value: number | null;
      evaluated_at: string | null;
      last_triggered_at: string | null;
      created_at: string;
    }>;
  }>("/api/v1/alerts");
}

export function createAlert(params: {
  lat: number;
  lng: number;
  condition: string;
  threshold: number;
  location_name?: string;
}) {
  return request<{
    id: string;
    latitude: number;
    longitude: number;
    condition: string;
    threshold: number;
    active: boolean;
    location_name: string | null;
    triggered: boolean;
    current_value: number | null;
    evaluated_at: string | null;
    last_triggered_at: string | null;
    created_at: string;
  }>("/api/v1/alerts", {
    method: "POST",
    body: JSON.stringify({
      latitude: params.lat,
      longitude: params.lng,
      condition: params.condition,
      threshold: params.threshold,
      location_name: params.location_name ?? null,
    }),
  });
}

// ── 9. Delete alert ───────────────────────────────────────────────────────────
// Backend returns 200 {message, id} (not 204)
export function deleteAlert(params: { id: string }) {
  return request<{ message: string; id: string }>(
    `/api/v1/alerts/${params.id}`,
    { method: "DELETE" }
  );
}

// ── 10. Evaluate alerts ───────────────────────────────────────────────────────
export function evaluateAlerts() {
  return request<{
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
  }>("/api/v1/alerts/evaluate", { method: "POST" });
}

// ── 11. Climate ───────────────────────────────────────────────────────────────
// Backend models: CMCC_CM2_VHR4 | EC_Earth3P_HR | MPI_ESM1_2_XR
export function getClimate(params: {
  lat: number;
  lng: number;
  start_date?: string;
  end_date?: string;
  model?: string;
}) {
  return request<{
    location: { latitude: number; longitude: number; name: string };
    period: { start: string; end: string };
    model: string;
    daily: Array<{
      date: string;
      temperature_mean: number;
      temperature_max: number;
      temperature_min: number;
      precipitation: number;
      humidity: number;
      wind_speed: number;
    }>;
    summary: {
      average_temperature: number;
      total_precipitation: number;
      average_humidity: number;
      average_wind_speed: number;
    };
    trend: { temperature: string; precipitation: string };
    data_source: { provider: string; dataset: string; attribution: string };
  }>(
    `/api/v1/climate${qs({
      latitude: params.lat,
      longitude: params.lng,
      start_date: params.start_date ?? "2025-01-01",
      end_date: params.end_date ?? "2025-01-30",
      model: params.model ?? "CMCC_CM2_VHR4",
    })}`
  );
}

// ── 12. Authentication ────────────────────────────────────────────────────────
import type { AuthResponse, AuthUser, DemoTokenResponse } from "@/lib/types";

export async function loginUser(body: { email: string; password: string }): Promise<AuthResponse> {
  const res = await request<AuthResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (res?.access_token) {
    setAuthToken(res.access_token);
  }
  return res;
}

export async function registerUser(body: {
  email: string;
  password: string;
  name?: string;
}): Promise<AuthResponse> {
  const res = await request<AuthResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (res?.access_token) {
    setAuthToken(res.access_token);
  }
  return res;
}

export async function logoutUser(): Promise<{ message: string }> {
  try {
    const res = await request<{ message: string }>("/api/v1/auth/logout", {
      method: "POST",
    });
    setAuthToken(null);
    return res;
  } catch {
    setAuthToken(null);
    return { message: "Logged out" };
  }
}

export async function getDemoToken(body?: {
  user_id?: string;
  email?: string;
}): Promise<DemoTokenResponse> {
  const res = await request<DemoTokenResponse>("/api/v1/auth/demo-token", {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });
  if (res?.access_token) {
    setAuthToken(res.access_token);
  }
  return res;
}

export function getMe(): Promise<AuthUser> {
  return request<AuthUser>("/api/v1/auth/me");
}

