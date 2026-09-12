"use client";

import { useLocationStore } from "@/lib/store";
import { useCurrentWeather } from "@/lib/hooks";
import { InlineError } from "@/components/inline-error";

export function CurrentConditions() {
  const { lat, lng, name } = useLocationStore();
  const { data, error, isLoading } = useCurrentWeather(lat, lng, name);

  if (lat === null || lng === null) {
    return (
      <section className="py-8">
        <p className="font-sans text-sm text-ink/50">
          Search for a location or enable location access
        </p>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section className="py-8">
        <div className="font-mono text-6xl text-ink/20">--°</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="py-8">
        <InlineError error={error} section="weather" />
      </section>
    );
  }

  if (!data) return null;

  // Real backend wraps in data.current
  const c = data.current;

  return (
    <section className="py-8">
      {/* Location name from backend reverse geocoding */}
      {data.location?.name && (
        <p className="font-sans text-xs text-ink/40 mb-1">{data.location.name}</p>
      )}

      {/* Dominant temperature */}
      <div className="font-mono text-6xl font-semibold text-ink leading-none">
        {c.temperature.toFixed(1)}°
      </div>

      {/* Condition text */}
      <p className="font-sans text-lg text-ink/70 mt-2">{c.condition}</p>

      {/* Quiet detail row */}
      <div className="flex flex-wrap gap-x-6 gap-y-1 mt-4">
        <Detail label="Feels like" value={`${c.feels_like.toFixed(1)}°`} />
        <Detail label="Humidity" value={`${c.humidity}%`} />
        <Detail label="Wind" value={`${c.wind_speed} km/h`} />
        <Detail label="Rain" value={`${c.rain_probability}%`} />
        {c.precipitation > 0 && (
          <Detail label="Precip." value={`${c.precipitation} mm`} />
        )}
      </div>
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-sm">
      <span className="font-sans text-ink/50">{label} </span>
      <span className="font-mono text-ink">{value}</span>
    </span>
  );
}
