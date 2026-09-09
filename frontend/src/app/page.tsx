/* ──────────────────────────────────────────────
 * Dashboard — / route
 * Top to bottom: location bar, current conditions,
 * hourly strip, 7-day forecast.
 * ────────────────────────────────────────────── */

"use client";

import { LocationBar } from "@/components/location-bar";
import { CurrentConditions } from "@/components/current-conditions";
import { HourlyStrip } from "@/components/hourly-strip";
import { ForecastList } from "@/components/forecast-list";

export default function Dashboard() {
  return (
    <main className="flex-1 overflow-y-auto w-full max-w-2xl mx-auto px-6 py-8">
      {/* Location bar */}
      <LocationBar />

      <hr className="border-t border-hairline mt-4" />

      {/* Current conditions */}
      <CurrentConditions />

      <hr className="border-t border-hairline" />

      {/* Hourly strip */}
      <HourlyStrip />

      <hr className="border-t border-hairline" />

      {/* 7-day forecast */}
      <ForecastList />
    </main>
  );
}
