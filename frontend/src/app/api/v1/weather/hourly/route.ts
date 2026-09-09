/* GET /api/v1/weather/hourly?lat=...&lng=...&date=... */

import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const lat = req.nextUrl.searchParams.get("lat");
  const lng = req.nextUrl.searchParams.get("lng");
  const date =
    req.nextUrl.searchParams.get("date") ??
    new Date().toISOString().split("T")[0];

  if (!lat || !lng) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_COORDINATES", message: "lat and lng are required" } } },
      { status: 422 }
    );
  }

  try {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&hourly=temperature_2m,precipitation_probability,weather_code&timezone=auto&start_date=${date}&end_date=${date}`;

    const res = await fetch(url, { next: { revalidate: 300 } });
    if (!res.ok) throw new Error("Open-Meteo unavailable");

    const data = await res.json();
    const hourly = data.hourly;

    const entries = hourly.time.map((time: string, i: number) => ({
      time,
      temperature: hourly.temperature_2m[i],
      condition: wmoToCondition(hourly.weather_code[i]),
      rain_probability: hourly.precipitation_probability[i] ?? 0,
    }));

    return NextResponse.json({
      location: `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`,
      date,
      hourly: entries,
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
