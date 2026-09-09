/* GET /api/v1/climate?lat=...&lng=...&start_date=...&end_date=...&model=... */

import { NextRequest, NextResponse } from "next/server";

const VALID_MODELS = ["ACCESS-CM2", "MPI-ESM1-2-HR", "EC-Earth3"];
const MAX_SPAN_DAYS = 365 * 10; // 10-year max

export async function GET(req: NextRequest) {
  const lat = req.nextUrl.searchParams.get("lat");
  const lng = req.nextUrl.searchParams.get("lng");
  const startDate = req.nextUrl.searchParams.get("start_date") ?? "2025-01-01";
  const endDate = req.nextUrl.searchParams.get("end_date") ?? "2025-01-30";
  const model = req.nextUrl.searchParams.get("model") ?? "ACCESS-CM2";

  if (!lat || !lng) {
    return NextResponse.json(
      { detail: { error: { code: "MISSING_COORDINATES", message: "lat and lng are required" } } },
      { status: 422 }
    );
  }

  // Validate model
  if (!VALID_MODELS.includes(model)) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_MODEL", message: `model must be one of: ${VALID_MODELS.join(", ")}` } } },
      { status: 400 }
    );
  }

  // Validate date format
  const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
  if (!dateRegex.test(startDate) || !dateRegex.test(endDate)) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_DATE_FORMAT", message: "Dates must be in YYYY-MM-DD format" } } },
      { status: 400 }
    );
  }

  const start = new Date(startDate);
  const end = new Date(endDate);

  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_DATE_FORMAT", message: "Invalid date values" } } },
      { status: 400 }
    );
  }

  if (end <= start) {
    return NextResponse.json(
      { detail: { error: { code: "INVALID_DATE_RANGE", message: "end_date must be after start_date" } } },
      { status: 400 }
    );
  }

  const spanDays = (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24);
  if (spanDays > MAX_SPAN_DAYS) {
    return NextResponse.json(
      { detail: { error: { code: "DATE_RANGE_TOO_LARGE", message: "Date range cannot exceed 10 years" } } },
      { status: 400 }
    );
  }

  try {
    // Use Open-Meteo Climate API (CMIP6)
    const modelSlug = model.toLowerCase();
    const url = `https://climate-api.open-meteo.com/v1/climate?latitude=${lat}&longitude=${lng}&start_date=${startDate}&end_date=${endDate}&models=${modelSlug}&daily=temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,wind_speed_10m_mean&timezone=auto`;

    const res = await fetch(url, { next: { revalidate: 3600 } });
    if (!res.ok) {
      // Fallback: try with the standard forecast API for recent dates
      return await fallbackToForecast(lat, lng, startDate, endDate, model);
    }

    const data = await res.json();
    const daily = data.daily;

    if (!daily?.time?.length) {
      return await fallbackToForecast(lat, lng, startDate, endDate, model);
    }

    const entries = daily.time.map((date: string, i: number) => ({
      date,
      temperature: daily.temperature_2m_mean?.[i] ?? 0,
      precipitation: daily.precipitation_sum?.[i] ?? 0,
      humidity: daily.relative_humidity_2m_mean?.[i] ?? 0,
      wind_speed: daily.wind_speed_10m_mean?.[i] ?? 0,
    }));

    // Calculate summary
    const temps = entries.map((e: { temperature: number }) => e.temperature);
    const precips = entries.map((e: { precipitation: number }) => e.precipitation);
    const humids = entries.map((e: { humidity: number }) => e.humidity);
    const winds = entries.map((e: { wind_speed: number }) => e.wind_speed);

    const avg = (arr: number[]) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
    const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0);

    // Simple trend calculation
    const firstHalf = temps.slice(0, Math.floor(temps.length / 2));
    const secondHalf = temps.slice(Math.floor(temps.length / 2));
    const tempTrend = avg(secondHalf) > avg(firstHalf) + 0.5
      ? "Warming"
      : avg(secondHalf) < avg(firstHalf) - 0.5
        ? "Cooling"
        : "Stable";

    const firstHalfPrecip = precips.slice(0, Math.floor(precips.length / 2));
    const secondHalfPrecip = precips.slice(Math.floor(precips.length / 2));
    const precipTrend = avg(secondHalfPrecip) > avg(firstHalfPrecip) * 1.2
      ? "Increasing"
      : avg(secondHalfPrecip) < avg(firstHalfPrecip) * 0.8
        ? "Decreasing"
        : "Stable";

    return NextResponse.json({
      location: `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`,
      model,
      start_date: startDate,
      end_date: endDate,
      daily: entries,
      summary: {
        avg_temperature: parseFloat(avg(temps).toFixed(1)),
        total_precipitation: parseFloat(sum(precips).toFixed(1)),
        avg_humidity: parseFloat(avg(humids).toFixed(1)),
        avg_wind_speed: parseFloat(avg(winds).toFixed(1)),
      },
      trend: {
        temperature: tempTrend,
        precipitation: precipTrend,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "CLIMATE_SERVICE_ERROR", message: "Couldn't reach the climate service" } } },
      { status: 502 }
    );
  }
}

async function fallbackToForecast(
  lat: string,
  lng: string,
  startDate: string,
  endDate: string,
  model: string
) {
  try {
    // Use the regular forecast API as a fallback for recent dates
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lng}&daily=temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean,wind_speed_10m_mean&start_date=${startDate}&end_date=${endDate}&timezone=auto`;

    const res = await fetch(url);
    if (!res.ok) throw new Error("Forecast fallback failed");

    const data = await res.json();
    const daily = data.daily;

    if (!daily?.time?.length) {
      return NextResponse.json(
        { detail: { error: { code: "CLIMATE_SERVICE_ERROR", message: "No data available for the selected range" } } },
        { status: 502 }
      );
    }

    const entries = daily.time.map((date: string, i: number) => ({
      date,
      temperature: daily.temperature_2m_mean?.[i] ?? 0,
      precipitation: daily.precipitation_sum?.[i] ?? 0,
      humidity: daily.relative_humidity_2m_mean?.[i] ?? 0,
      wind_speed: daily.wind_speed_10m_mean?.[i] ?? 0,
    }));

    const temps = entries.map((e: { temperature: number }) => e.temperature);
    const precips = entries.map((e: { precipitation: number }) => e.precipitation);
    const humids = entries.map((e: { humidity: number }) => e.humidity);
    const winds = entries.map((e: { wind_speed: number }) => e.wind_speed);

    const avg = (arr: number[]) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
    const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0);

    return NextResponse.json({
      location: `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`,
      model,
      start_date: startDate,
      end_date: endDate,
      daily: entries,
      summary: {
        avg_temperature: parseFloat(avg(temps).toFixed(1)),
        total_precipitation: parseFloat(sum(precips).toFixed(1)),
        avg_humidity: parseFloat(avg(humids).toFixed(1)),
        avg_wind_speed: parseFloat(avg(winds).toFixed(1)),
      },
      trend: {
        temperature: "Stable",
        precipitation: "Stable",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: { error: { code: "CLIMATE_SERVICE_ERROR", message: "Couldn't reach the climate service" } } },
      { status: 502 }
    );
  }
}
