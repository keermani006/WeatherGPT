/* ──────────────────────────────────────────────
 * TanStack Query hooks for Dashboard
 * Covers: current weather, hourly, forecast, location search
 * ────────────────────────────────────────────── */

"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getCurrentWeather,
  getHourlyWeather,
  getForecast,
  searchLocations,
} from "@/lib/api";

/** Current conditions — enabled only when we have coordinates. */
export function useCurrentWeather(lat: number | null, lng: number | null) {
  return useQuery({
    queryKey: ["weather", "current", lat, lng],
    queryFn: () => getCurrentWeather({ lat: lat!, lng: lng! }),
    enabled: lat !== null && lng !== null,
    retry: 1,
  });
}

/** Hourly forecast (next 24 h) — date = today. */
export function useHourlyWeather(lat: number | null, lng: number | null) {
  const today = new Date().toISOString().split("T")[0];
  return useQuery({
    queryKey: ["weather", "hourly", lat, lng, today],
    queryFn: () =>
      getHourlyWeather({ lat: lat!, lng: lng!, date: today }),
    enabled: lat !== null && lng !== null,
    retry: 1,
  });
}

/** 7-day forecast — days hardcoded to 7. */
export function useForecast(lat: number | null, lng: number | null) {
  return useQuery({
    queryKey: ["weather", "forecast", lat, lng],
    queryFn: () => getForecast({ lat: lat!, lng: lng!, days: 7 }),
    enabled: lat !== null && lng !== null,
    retry: 1,
  });
}

/** Location search — enabled only when query is non-empty. */
export function useLocationSearch(query: string) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: ["location", "search", trimmed],
    queryFn: () => searchLocations({ query: trimmed }),
    enabled: trimmed.length > 0,
    staleTime: 30_000,
  });
}
