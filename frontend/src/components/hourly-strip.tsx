"use client";

import { useLocationStore } from "@/lib/store";
import { useHourlyWeather } from "@/lib/hooks";
import { InlineError } from "@/components/inline-error";

export function HourlyStrip() {
  const { lat, lng } = useLocationStore();
  const { data, error, isLoading } = useHourlyWeather(lat, lng);

  if (lat === null || lng === null) return null;

  if (isLoading) {
    return (
      <section className="py-6">
        <p className="font-sans text-sm text-ink/30">Loading hourly forecast…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="py-6">
        <InlineError error={error} section="hourly" />
      </section>
    );
  }

  if (!data?.hourly?.length) return null;

  const maxRain = Math.max(...data.hourly.map((h) => h.rain_probability), 1);

  return (
    <section className="py-6">
      <h2 className="font-sans text-sm text-ink/50 mb-3">Next 24 hours</h2>

      <div className="hourly-strip overflow-x-auto -mx-6 px-6">
        <div
          className="flex gap-0 min-w-max"
          role="list"
          aria-label="Hourly forecast"
          aria-live="polite"
        >
          {data.hourly.map((hour, i) => {
            // Backend returns time as "HH:MM" string (not ISO datetime)
            const label = hour.time;
            const rainHeight = Math.max((hour.rain_probability / maxRain) * 24, 1);

            return (
              <div
                key={`${hour.time}-${i}`}
                role="listitem"
                className={`flex flex-col items-center px-3 py-2 ${
                  i > 0 ? "border-l border-hairline" : ""
                }`}
              >
                <span className="font-sans text-xs text-ink/50">{label}</span>

                <span className="font-mono text-sm text-ink mt-1">
                  {hour.temperature.toFixed(0)}°
                </span>

                <div className="w-6 mt-2 flex flex-col items-center">
                  <div
                    className="w-full bg-teal/60 rounded-sm"
                    style={{ height: `${rainHeight}px` }}
                    title={`${hour.rain_probability}% rain`}
                  />
                </div>

                <span className="font-mono text-[10px] text-teal/70 mt-1">
                  {hour.rain_probability}%
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
