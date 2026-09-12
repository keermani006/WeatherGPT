"use client";

/* ─────────────────────────────────────────────────────────────
 * Travel Weather Card
 * Returned when implicit or explicit route/travel intent is detected.
 * Includes:
 *   1. Origin → Destination
 *   2. Overall travel weather risk (Low / Moderate / High)
 *   3. Route weather with pass-by waypoints & rain severity
 *   4. Timing evaluation / disclaimer
 *   5. Cargo-specific protection advice (e.g. harvested rice/crops)
 *   6. Practical, supported recommendations
 * ───────────────────────────────────────────────────────────── */

import React from "react";
import type { RouteWaypoint, TravelCardData, WeatherData } from "@/lib/types";

interface RouteItineraryCardProps {
  travelCard?: TravelCardData;
  originName: string;
  originWeather?: WeatherData;
  destinationName: string;
  destinationWeather?: WeatherData;
  waypoints?: RouteWaypoint[];
}

export function RouteItineraryCard({
  travelCard,
  originName,
  originWeather,
  destinationName,
  destinationWeather,
  waypoints = [],
}: RouteItineraryCardProps) {
  const origin = travelCard?.origin || originWeather?.location || originName || "Origin";
  const destination = travelCard?.destination || destinationWeather?.location || destinationName || "Destination";
  const routeWaypoints = (travelCard?.waypoints && travelCard.waypoints.length > 0)
    ? travelCard.waypoints
    : waypoints;

  // Derive risk level and styling
  const overallRisk = travelCard?.overall_risk || (
    Math.max(
      originWeather?.rain_probability ?? 0,
      destinationWeather?.rain_probability ?? 0,
      ...routeWaypoints.map((w) => w.weather?.rain_probability ?? 0)
    ) >= 60
      ? "High"
      : Math.max(
          originWeather?.rain_probability ?? 0,
          destinationWeather?.rain_probability ?? 0,
          ...routeWaypoints.map((w) => w.weather?.rain_probability ?? 0)
        ) >= 25
      ? "Moderate"
      : "Low"
  );

  let riskBadgeStyle = "text-emerald-400 border-emerald-500/30 bg-emerald-500/10";
  let riskIcon = "🟢";
  if (overallRisk === "High") {
    riskBadgeStyle = "text-rose-400 border-rose-500/30 bg-rose-500/10";
    riskIcon = "🔴";
  } else if (overallRisk === "Moderate") {
    riskBadgeStyle = "text-amber-400 border-amber-500/30 bg-amber-500/10";
    riskIcon = "🟡";
  }

  return (
    <div
      id="travel-weather-card"
      className="mt-3 rounded-lg border border-hairline/90 bg-paper/80 backdrop-blur-md p-4 shadow-xl shadow-black/25 text-ink space-y-4"
    >
      {/* ── 1. Header: Origin → Destination & Overall Risk ── */}
      <div className="flex flex-wrap items-start justify-between gap-3 pb-3 border-b border-hairline/70">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-widest text-isobar font-semibold flex items-center gap-1.5">
              <span>🛣️</span> Travel Weather Card
            </span>
            {travelCard?.cargo && (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-medium border border-teal/40 bg-teal/10 text-teal">
                📦 {travelCard.cargo}
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-2 font-sans text-base font-bold text-ink">
            <span>{origin}</span>
            <span className="text-isobar font-mono text-xs">→</span>
            <span>{destination}</span>
          </div>
        </div>

        {/* ── 2. Overall Travel Weather Risk ── */}
        <div className="flex flex-col items-end">
          <div className="text-[10px] font-mono text-ink/50 uppercase tracking-wider mb-1">
            Overall Route Risk
          </div>
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono font-semibold border ${riskBadgeStyle}`}
          >
            <span>{riskIcon}</span> {overallRisk} Risk
          </span>
        </div>
      </div>

      {/* Risk Summary Subtext */}
      {travelCard?.risk_summary && (
        <div className="text-xs font-sans text-ink/80 bg-ink/5 rounded px-3 py-2 border border-hairline/50">
          <span className="font-semibold text-ink font-mono text-[11px]">Route Assessment:</span>{" "}
          {travelCard.risk_summary}
        </div>
      )}

      {/* ── 3. Route Weather & Intermediate Waypoints Corridor ── */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-wider text-ink/70 font-semibold">
            Route Weather Corridor
          </span>
          {travelCard?.route_weather_summary && (
            <span className="font-sans text-[11px] text-ink/60 truncate max-w-[65%]">
              {travelCard.route_weather_summary}
            </span>
          )}
        </div>

        {/* Corridor Timeline / Path */}
        <div className="relative pl-6 space-y-4 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-gradient-to-b before:from-emerald-500 before:via-isobar before:to-sky-500">
          {/* Origin Point */}
          <div className="relative">
            <div className="absolute -left-[23px] top-1.5 w-3 h-3 rounded-full bg-emerald-500 ring-4 ring-paper border-2 border-emerald-300" />
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="px-1.5 py-0.2 text-[9px] font-mono uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded">
                  Origin
                </span>
                <span className="font-sans text-xs font-semibold text-ink">{origin}</span>
              </div>
              {originWeather && (
                <div className="flex items-center gap-3 font-mono text-xs">
                  <span className="font-semibold text-ink">{originWeather.temperature}°C</span>
                  <span className="text-ink/60 text-[11px]">{originWeather.condition}</span>
                  {originWeather.rain_probability != null && (
                    <span className="text-ink/60 text-[11px]">
                      💧 {originWeather.rain_probability}%
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Intermediate Waypoints */}
          {routeWaypoints.map((wp, idx) => {
            const rain = wp.weather?.rain_probability ?? 0;
            const cond = (wp.weather?.condition || "").toLowerCase();
            let rainSeverity = "Clear / Dry";
            let badgeColor = "text-ink/60 border-hairline bg-ink/5";

            if (rain >= 60 || cond.includes("heavy") || cond.includes("thunderstorm")) {
              rainSeverity = "Heavy Rain / Storm";
              badgeColor = "text-rose-400 border-rose-500/30 bg-rose-500/10";
            } else if (rain >= 25 || cond.includes("rain") || cond.includes("shower") || cond.includes("drizzle")) {
              rainSeverity = "Light / Moderate Rain";
              badgeColor = "text-amber-400 border-amber-500/30 bg-amber-500/10";
            }

            return (
              <div
                key={idx}
                className="relative p-2.5 rounded bg-isobar/5 border border-hairline/70 hover:border-isobar/40 transition-colors"
              >
                <div className="absolute -left-[23px] top-3 w-2.5 h-2.5 rounded-full bg-isobar ring-4 ring-paper" />

                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-isobar font-semibold">
                      Stop {idx + 1}
                    </span>
                    <span className="font-sans text-xs font-medium text-ink">{wp.name}</span>
                    {wp.distance_km != null && (
                      <span className="text-[10px] font-mono text-ink/40">
                        (~{wp.distance_km} km)
                      </span>
                    )}
                  </div>

                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border ${badgeColor}`}
                  >
                    {rainSeverity}
                  </span>
                </div>

                {wp.weather && (
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono text-ink/70">
                    <div className="flex items-center gap-1">
                      <span className="text-ink font-semibold">{wp.weather.temperature}°C</span>
                      <span className="text-[11px] text-ink/50">({wp.weather.condition})</span>
                    </div>
                    {wp.weather.rain_probability != null && (
                      <div className="flex items-center gap-1">
                        <span className="text-ink/40 text-[10px]">Rain:</span>
                        <span
                          className={
                            rain >= 50
                              ? "text-rose-400 font-semibold"
                              : rain >= 25
                              ? "text-amber-400"
                              : "text-ink/60"
                          }
                        >
                          {wp.weather.rain_probability}%
                        </span>
                      </div>
                    )}
                    {wp.weather.wind_speed != null && (
                      <div className="flex items-center gap-1">
                        <span className="text-ink/40 text-[10px]">Wind:</span>
                        <span>{wp.weather.wind_speed} m/s</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* Destination Point */}
          <div className="relative">
            <div className="absolute -left-[23px] top-1.5 w-3 h-3 rounded-full bg-sky-500 ring-4 ring-paper border-2 border-sky-300" />
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="px-1.5 py-0.2 text-[9px] font-mono uppercase tracking-wider bg-sky-500/10 text-sky-400 border border-sky-500/20 rounded">
                  Destination
                </span>
                <span className="font-sans text-xs font-semibold text-ink">{destination}</span>
              </div>
              {destinationWeather && (
                <div className="flex items-center gap-3 font-mono text-xs">
                  <span className="font-semibold text-ink">{destinationWeather.temperature}°C</span>
                  <span className="text-ink/60 text-[11px]">{destinationWeather.condition}</span>
                  {destinationWeather.rain_probability != null && (
                    <span className="text-ink/60 text-[11px]">
                      💧 {destinationWeather.rain_probability}%
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── 4. Timing ── */}
      <div className="rounded p-2.5 bg-isobar/5 border border-hairline/60 flex items-start gap-2.5">
        <span className="text-sm">⏱️</span>
        <div className="text-xs">
          <div className="font-mono text-[10px] uppercase tracking-wider text-isobar font-semibold">
            {travelCard?.departure_timing ? `Timing: ${travelCard.departure_timing}` : "Departure Timing"}
          </div>
          <p className="font-sans text-ink/70 mt-0.5 leading-relaxed">
            {travelCard?.timing_note ||
              "Risk evaluated for upcoming forecast window. Actual route weather depends on your exact departure timing."}
          </p>
        </div>
      </div>

      {/* ── 5. Cargo-Specific Risk ── */}
      {travelCard?.cargo_risk_advice && (
        <div className="rounded p-3 bg-amber-500/10 border border-amber-500/30 flex items-start gap-2.5">
          <span className="text-sm">🌾</span>
          <div className="text-xs">
            <div className="font-mono text-[10px] uppercase tracking-wider text-amber-400 font-semibold">
              Cargo Protection: {travelCard.cargo || "Sensitive Goods"}
            </div>
            <p className="font-sans text-ink/90 mt-1 leading-relaxed">
              {travelCard.cargo_risk_advice}
            </p>
          </div>
        </div>
      )}

      {/* ── 6. Practical Recommendation ── */}
      {travelCard?.recommendation && (
        <div className="rounded p-3 bg-teal/10 border border-teal/30 flex items-start gap-2.5">
          <span className="text-sm">💡</span>
          <div className="text-xs">
            <div className="font-mono text-[10px] uppercase tracking-wider text-teal font-semibold">
              Practical Route Recommendation
            </div>
            <p className="font-sans text-ink font-medium mt-1 leading-relaxed">
              {travelCard.recommendation}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
