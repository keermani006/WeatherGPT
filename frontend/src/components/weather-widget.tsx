/* ──────────────────────────────────────────────
 * Weather Data Widget
 * Inline widget rendered next to assistant chat bubbles.
 * Styled consistently with dashboard data readouts
 * (tabular-mono numbers, not a new visual style).
 * ────────────────────────────────────────────── */

import type { WeatherData } from "@/lib/types";

interface WeatherWidgetProps {
  data: WeatherData;
}

export function WeatherWidget({ data }: WeatherWidgetProps) {
  return (
    <div className="mt-2 border-l-2 border-isobar pl-3 py-1">
      {data.location && (
        <p className="font-sans text-xs text-ink/50 mb-1">{data.location}</p>
      )}

      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {data.temperature != null && (
          <span className="text-sm">
            <span className="font-sans text-ink/50">Temp </span>
            <span className="font-mono text-ink">{data.temperature}°</span>
          </span>
        )}

        {data.feels_like != null && (
          <span className="text-sm">
            <span className="font-sans text-ink/50">Feels </span>
            <span className="font-mono text-ink">{data.feels_like}°</span>
          </span>
        )}

        {data.condition && (
          <span className="text-sm">
            <span className="font-sans text-ink/50">Cond. </span>
            <span className="font-mono text-ink">{data.condition}</span>
          </span>
        )}

        {data.humidity != null && (
          <span className="text-sm">
            <span className="font-sans text-ink/50">Hum. </span>
            <span className="font-mono text-ink">{data.humidity}%</span>
          </span>
        )}

        {data.wind_speed && (
          <span className="text-sm">
            <span className="font-sans text-ink/50">Wind </span>
            <span className="font-mono text-ink">{data.wind_speed}</span>
          </span>
        )}
      </div>
    </div>
  );
}
