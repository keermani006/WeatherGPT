/* POST /api/v1/alerts — Create an alert */
/* GET  /api/v1/alerts — List all alerts  */

import { NextRequest, NextResponse } from "next/server";

// In-memory store (resets on server restart — fine for SIH demo)
export interface StoredAlert {
  id: string;
  condition: string;
  threshold: number;
  lat: number;
  lng: number;
  location_name: string;
  triggered: boolean;
  current_value?: number;
  evaluated_at?: string;
  created_at: string;
}

// Use global to persist across hot reloads in dev
const globalAlerts = globalThis as unknown as { __alerts?: StoredAlert[] };
if (!globalAlerts.__alerts) globalAlerts.__alerts = [];

function getAlerts(): StoredAlert[] {
  return globalAlerts.__alerts!;
}

const VALID_CONDITIONS = [
  "temperature_above",
  "temperature_below",
  "rain_probability_above",
  "wind_speed_above",
];

const THRESHOLD_RANGES: Record<string, { min: number; max: number }> = {
  temperature_above: { min: -50, max: 60 },
  temperature_below: { min: -50, max: 60 },
  rain_probability_above: { min: 0, max: 100 },
  wind_speed_above: { min: 0, max: 200 },
};

export async function GET() {
  return NextResponse.json({ alerts: getAlerts() });
}

export async function POST(req: NextRequest) {
  let body: {
    condition?: string;
    threshold?: number;
    lat?: number;
    lng?: number;
    location_name?: string;
  };

  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_REQUEST", message: "Invalid JSON body" } } },
      { status: 400 }
    );
  }

  const { condition, threshold, lat, lng, location_name } = body;

  if (!condition || !VALID_CONDITIONS.includes(condition)) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_CONDITION", message: "condition must be one of: " + VALID_CONDITIONS.join(", ") } } },
      { status: 400 }
    );
  }

  if (threshold == null || typeof threshold !== "number") {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_THRESHOLD", message: "threshold must be a number" } } },
      { status: 400 }
    );
  }

  const range = THRESHOLD_RANGES[condition];
  if (threshold < range.min || threshold > range.max) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_THRESHOLD", message: `threshold for ${condition} must be between ${range.min} and ${range.max}` } } },
      { status: 400 }
    );
  }

  if (lat == null || lng == null) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_COORDINATES", message: "lat and lng are required" } } },
      { status: 422 }
    );
  }

  const alert: StoredAlert = {
    id: crypto.randomUUID(),
    condition,
    threshold,
    lat,
    lng,
    location_name: location_name ?? `${lat.toFixed(2)}°, ${lng.toFixed(2)}°`,
    triggered: false,
    created_at: new Date().toISOString(),
  };

  getAlerts().push(alert);

  return NextResponse.json(alert, { status: 201 });
}
