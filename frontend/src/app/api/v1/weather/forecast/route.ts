/* GET /api/v1/weather/forecast?lat=...&lng=...&days=7 */

import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const lat = req.nextUrl.searchParams.get("lat");
  const lng = req.nextUrl.searchParams.get("lng");
  const days = req.nextUrl.searchParams.get("days") ?? "7";

  if (!lat || !lng) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_COORDINATES", message: "lat and lng are required" } } },
      { status: 422 }
    );
  }

  try {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code&timezone=auto&forecast_days=${days}`;

    const res = await fetch(url, { next: { revalidate: 300 } });
    if (!res.ok) throw new Error("Open-Meteo unavailable");

    const data = await res.json();
    const daily = data.daily;

    const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

    const forecast = daily.time.map((date: string, i: number) => {
      const d = new Date(date + "T00:00:00");
      return {
        date,
        day_name: dayNames[d.getDay()],
        condition: wmoToCondition(daily.weather_code[i]),
        high: daily.temperature_2m_max[i],
        low: daily.temperature_2m_min[i],
        rain_probability: daily.precipitation_probability_max[i] ?? 0,
      };
    });

    return NextResponse.json({
      location: `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`,
      forecast,
    });
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "WEATHER_SERVICE_ERROR", message: "Couldn't reach the weather service" } } },
      { status: 502 }
    );
  }
}

function wmoToCondition(code: number): string {
  const map: Record<number, string> = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm",
  };
  return map[code] ?? "Unknown";
}
