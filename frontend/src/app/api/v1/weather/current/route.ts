/* GET /api/v1/weather/current?lat=...&lng=... */

import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const lat = req.nextUrl.searchParams.get("lat");
  const lng = req.nextUrl.searchParams.get("lng");

  if (!lat || !lng) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_COORDINATES", message: "lat and lng are required" } } },
      { status: 422 }
    );
  }

  try {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code,precipitation_probability&timezone=auto`;

    const res = await fetch(url, { next: { revalidate: 300 } });
    if (!res.ok) throw new Error("Open-Meteo unavailable");

    const data = await res.json();
    const current = data.current;

    // Reverse geocode for location name
    let location = `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`;
    try {
      const geoRes = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${lat},${lng}&count=1&language=en`
      );
      if (geoRes.ok) {
        const geoData = await geoRes.json();
        if (geoData.results?.[0]?.name) {
          location = geoData.results[0].name;
        }
      }
    } catch {
      // fallback to coordinates
    }

    // Map WMO weather codes to condition text
    const condition = wmoToCondition(current.weather_code);

    // Wind direction from degrees
    const windDir = degreesToDirection(current.wind_direction_10m);

    return NextResponse.json({
      location,
      temperature: current.temperature_2m,
      feels_like: current.apparent_temperature,
      condition,
      humidity: current.relative_humidity_2m,
      wind_speed: current.wind_speed_10m,
      wind_direction: windDir,
      rain_probability: current.precipitation_probability ?? 0,
      timestamp: current.time,
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
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
  };
  return map[code] ?? "Unknown";
}

function degreesToDirection(deg: number): string {
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const idx = Math.round(deg / 45) % 8;
  return dirs[idx];
}
