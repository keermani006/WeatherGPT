"use client";

import { useLocationStore } from "@/lib/store";
import { useForecast } from "@/lib/hooks";
import { InlineError } from "@/components/inline-error";

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function ForecastList() {
  const { lat, lng, name } = useLocationStore();
  const { data, error, isLoading } = useForecast(lat, lng, name);

  if (lat === null || lng === null) return null;

  if (isLoading) {
    return (
      <section className="py-6">
        <p className="font-sans text-sm text-ink/30">Loading forecast…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="py-6">
        <InlineError error={error} section="forecast" />
      </section>
    );
  }

  if (!data?.forecast?.length) return null;

  return (
    <section className="py-6">
      <h2 className="font-sans text-sm text-ink/50 mb-3">7-day forecast</h2>

      <ul role="list">
        {data.forecast.map((day, i) => {
          // Backend returns temperature_max / temperature_min (not high/low)
          // Derive day name from date string
          const d = new Date(day.date + "T00:00:00");
          const dayName = DAY_NAMES[d.getDay()];

          return (
            <li
              key={day.date}
              className={`flex items-center justify-between py-2.5 sm:py-3 ${
                i > 0 ? "border-t border-hairline" : ""
              }`}
            >
              <span className="font-sans text-xs sm:text-sm text-ink font-medium w-20 sm:w-24 shrink-0">
                {dayName}
              </span>

              <span className="font-sans text-xs sm:text-sm text-ink/60 flex-1 text-center truncate px-2">
                {day.condition}
              </span>

              <span className="font-mono text-xs sm:text-sm text-ink shrink-0 w-16 sm:w-20 text-right">
                {day.temperature_max.toFixed(0)}° / {day.temperature_min.toFixed(0)}°
              </span>

              <span className="font-mono text-xs sm:text-sm text-teal shrink-0 w-10 sm:w-12 text-right">
                {day.rain_probability}%
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
