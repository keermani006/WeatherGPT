/* POST /api/v1/alerts/evaluate — Check all alerts against current weather */

import { NextResponse } from "next/server";
import type { StoredAlert } from "../route";

const globalAlerts = globalThis as unknown as { __alerts?: StoredAlert[] };

export async function POST() {
  const alerts = globalAlerts.__alerts ?? [];

  if (alerts.length === 0) {
    return NextResponse.json({ alerts: [] });
  }

  // Group alerts by location to minimize API calls
  const locationGroups = new Map<string, StoredAlert[]>();
  for (const alert of alerts) {
    const key = `${alert.lat},${alert.lng}`;
    if (!locationGroups.has(key)) locationGroups.set(key, []);
    locationGroups.get(key)!.push(alert);
  }

  // Fetch current weather for each unique location
  for (const [key, group] of locationGroups) {
    const [lat, lng] = key.split(",");
    try {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&current=temperature_2m,precipitation_probability,wind_speed_10m&timezone=auto`;
      const res = await fetch(url);
      if (!res.ok) continue;

      const data = await res.json();
      const current = data.current;
      const now = new Date().toISOString();

      for (const alert of group) {
        alert.evaluated_at = now;

        switch (alert.condition) {
          case "temperature_above":
            alert.current_value = current.temperature_2m;
            alert.triggered = current.temperature_2m > alert.threshold;
            break;
          case "temperature_below":
            alert.current_value = current.temperature_2m;
            alert.triggered = current.temperature_2m < alert.threshold;
            break;
          case "rain_probability_above":
            alert.current_value = current.precipitation_probability ?? 0;
            alert.triggered = (current.precipitation_probability ?? 0) > alert.threshold;
            break;
          case "wind_speed_above":
            alert.current_value = current.wind_speed_10m;
            alert.triggered = current.wind_speed_10m > alert.threshold;
            break;
        }
      }
    } catch {
      // If a location fails, skip it — don't fail the whole evaluation
    }
  }

  return NextResponse.json({ alerts });
}
