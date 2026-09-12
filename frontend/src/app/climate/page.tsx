/* ──────────────────────────────────────────────
 * Climate Page — /climate
 * Date range inputs, CMIP6 model selector, chart,
 * summary stats, trend block, and mandatory attribution.
 * ────────────────────────────────────────────── */

"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocationStore } from "@/lib/store";
import { getClimate, ApiError } from "@/lib/api";
import type { ClimateModel } from "@/lib/types";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from "recharts";

const MODELS: { value: ClimateModel; label: string }[] = [
  { value: "CMCC_CM2_VHR4", label: "CMCC-CM2-VHR4" },
  { value: "EC_Earth3P_HR", label: "EC-Earth3P-HR" },
  { value: "MPI_ESM1_2_XR", label: "MPI-ESM1-2-XR" },
];

export default function ClimatePage() {
  const { lat, lng, name: locationName } = useLocationStore();
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-01-30");
  const [model, setModel] = useState<ClimateModel>("CMCC_CM2_VHR4");

  // Client-side validation — derived, no setState
  const { isValid, validationError } = useMemo(() => {
    if (!startDate || !endDate) {
      return { isValid: false, validationError: "Both dates are required" };
    }

    const start = new Date(startDate);
    const end = new Date(endDate);

    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
      return { isValid: false, validationError: "Invalid date format — use YYYY-MM-DD" };
    }

    if (end <= start) {
      return { isValid: false, validationError: "End date must be after start date" };
    }

    const spanDays = (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24);
    if (spanDays > 365 * 10) {
      return { isValid: false, validationError: "Date range cannot exceed 10 years" };
    }

    return { isValid: true, validationError: null };
  }, [startDate, endDate]);

  const canFetch = isValid && lat !== null && lng !== null;

  const { data, error, isLoading } = useQuery({
    queryKey: ["climate", lat, lng, startDate, endDate, model],
    queryFn: () =>
      getClimate({
        lat: lat!,
        lng: lng!,
        start_date: startDate,
        end_date: endDate,
        model,
      }),
    enabled: canFetch,
    retry: 1,
  });

  // Map API error to user-friendly message
  const apiError = error instanceof ApiError ? error : null;
  const errorMessage =
    apiError?.code === "CLIMATE_SERVICE_ERROR" || apiError?.code === "CLIMATE_SERVICE_TIMEOUT"
      ? "Couldn't reach the climate service — retrying shortly."
      : apiError?.message ?? (error ? "An unexpected error occurred." : null);

  return (
    <main className="flex-1 overflow-y-auto w-full max-w-2xl md:max-w-3xl mx-auto px-4 sm:px-6 py-5 sm:py-8">
      <h1 className="font-sans text-2xl font-semibold text-ink">Climate</h1>
      <p className="font-sans text-sm text-ink/50 mt-1">
        CMIP6 model projections{locationName && <span> for {locationName}</span>}
      </p>
      <p className="font-sans text-xs text-ink/40 mt-0.5">
        These are statistical climate model projections — not day-to-day weather forecasts.
        Dates may extend into the future.
      </p>

      <hr className="border-t border-hairline mt-4 mb-6" />

      {/* ── Controls ── */}
      <section className="flex flex-col sm:flex-row flex-wrap sm:items-end gap-3 sm:gap-4">
        {/* Start date */}
        <div className="w-full sm:w-auto">
          <label className="block font-sans text-xs text-ink/50 mb-1">
            Start date
          </label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-full sm:w-auto bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-mono text-ink focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
          />
        </div>

        {/* End date */}
        <div className="w-full sm:w-auto">
          <label className="block font-sans text-xs text-ink/50 mb-1">
            End date
          </label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-full sm:w-auto bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-base sm:text-sm font-mono text-ink focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
          />
        </div>

        {/* Model selector */}
        <div className="w-full sm:w-auto">
          <label className="block font-sans text-xs text-ink/50 mb-1">
            Model
          </label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value as ClimateModel)}
            className="w-full sm:w-auto bg-transparent border-0 border-b border-hairline px-1 py-1.5 sm:py-2 text-sm font-sans text-ink focus:border-isobar outline-none ring-0 shadow-none focus:outline-none focus:ring-0 transition-colors"
          >
            {MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      </section>

      {/* Validation error — inline under date inputs */}
      {validationError && (
        <p className="font-sans text-sm text-ochre mt-2">{validationError}</p>
      )}

      {/* No location */}
      {lat === null && (
        <p className="font-sans text-sm text-ink/50 mt-4">
          Set a location first — use the dashboard to search or enable geolocation
        </p>
      )}

      {/* API error */}
      {errorMessage && !validationError && (
        <p className="font-sans text-sm text-ochre mt-4">{errorMessage}</p>
      )}

      {/* Loading */}
      {isLoading && (
        <p className="font-sans text-sm text-ink/30 mt-6">Loading climate data…</p>
      )}

      {/* ── Chart + Data ── */}
      {data && (
        <>
          <hr className="border-t border-hairline mt-6 mb-4" />

          {/* Chart */}
          <section className="mt-4">
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={data.daily} margin={{ top: 5, right: 5, bottom: 5, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#C9C4B8" opacity={0.5} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fontFamily: "var(--font-ibm-plex-mono)", fill: "#131B22" }}
                    tickFormatter={(d: string) => {
                      const date = new Date(d);
                      return `${date.getMonth() + 1}/${date.getDate()}`;
                    }}
                    stroke="#C9C4B8"
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    yAxisId="temp"
                    tick={{ fontSize: 10, fontFamily: "var(--font-ibm-plex-mono)", fill: "#131B22" }}
                    stroke="#C9C4B8"
                    label={{ value: "°C", position: "insideTopLeft", fontSize: 10, fill: "#1F4E66" }}
                  />
                  <YAxis
                    yAxisId="precip"
                    orientation="right"
                    tick={{ fontSize: 10, fontFamily: "var(--font-ibm-plex-mono)", fill: "#131B22" }}
                    stroke="#C9C4B8"
                    label={{ value: "mm", position: "insideTopRight", fontSize: 10, fill: "#2E6F63" }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#EEECE6",
                      border: "1px solid #C9C4B8",
                      borderRadius: 0,
                      fontFamily: "var(--font-ibm-plex-mono)",
                      fontSize: 12,
                    }}
                    labelFormatter={(d) => new Date(String(d)).toLocaleDateString()}
                  />
                  <Legend
                    wrapperStyle={{ fontSize: 11, fontFamily: "Inter, sans-serif" }}
                  />
                  <Line
                    yAxisId="temp"
                    type="monotone"
                    dataKey="temperature_mean"
                    stroke="#1F4E66"
                    strokeWidth={2}
                    dot={false}
                    name="Temperature (°C)"
                  />
                  <Bar
                    yAxisId="precip"
                    dataKey="precipitation"
                    fill="#2E6F63"
                    opacity={0.6}
                    name="Precipitation (mm)"
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </section>

          <hr className="border-t border-hairline mt-4 mb-4" />

          {/* Summary */}
          <section>
            <h2 className="font-sans text-sm text-ink/50 mb-2">Summary</h2>
            <div className="flex flex-wrap gap-x-8 gap-y-2">
              <Stat label="Avg. temperature" value={`${data.summary.average_temperature}°C`} />
              <Stat label="Total precipitation" value={`${data.summary.total_precipitation} mm`} />
              <Stat label="Avg. humidity" value={`${data.summary.average_humidity}%`} />
              <Stat label="Avg. wind speed" value={`${data.summary.average_wind_speed} km/h`} />
            </div>
          </section>

          <hr className="border-t border-hairline mt-4 mb-4" />

          {/* Trend */}
          <section>
            <h2 className="font-sans text-sm text-ink/50 mb-2">Trend</h2>
            <div className="flex flex-wrap gap-x-8 gap-y-1">
              <span className="text-sm">
                <span className="font-sans text-ink/50">Temperature: </span>
                <span className="font-sans text-ink">{data.trend.temperature}</span>
              </span>
              <span className="text-sm">
                <span className="font-sans text-ink/50">Precipitation: </span>
                <span className="font-sans text-ink">{data.trend.precipitation}</span>
              </span>
            </div>
          </section>

          <hr className="border-t border-hairline mt-4 mb-4" />

          {/* ── MANDATORY attribution from backend data_source ── */}
          <p className="font-sans text-xs text-ink/40">
            Data: {data.data_source?.attribution ?? "Open-Meteo / CMIP6"}
          </p>
        </>
      )}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-sm">
      <span className="font-sans text-ink/50">{label} </span>
      <span className="font-mono text-ink">{value}</span>
    </span>
  );
}
