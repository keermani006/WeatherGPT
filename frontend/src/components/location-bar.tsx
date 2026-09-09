/* ──────────────────────────────────────────────
 * Location Bar
 * - Resolved location name display
 * - Debounced (400ms) search autocomplete
 * - Browser geolocation button
 * ────────────────────────────────────────────── */

"use client";

import { useState, useEffect, useRef } from "react";
import { useLocationStore } from "@/lib/store";
import { useLocationSearch } from "@/lib/hooks";
import { InlineError } from "@/components/inline-error";
import { GpsIcon } from "@/components/icons";

export function LocationBar() {
  const { name, setLocation } = useLocationStore();
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [showResults, setShowResults] = useState(false);
  const [geoLoading, setGeoLoading] = useState(false);
  const geoAttempted = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const { data, error, isLoading } = useLocationSearch(debouncedQuery);

  // ── Debounce search input (400ms) ──
  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      // Clear via the timeout to avoid synchronous setState in effect
      const clear = setTimeout(() => setDebouncedQuery(""), 0);
      return () => clearTimeout(clear);
    }
    const timer = setTimeout(() => setDebouncedQuery(query), 400);
    return () => clearTimeout(timer);
  }, [query]);

  // ── Auto-geolocation on first load ──
  useEffect(() => {
    if (geoAttempted.current) return;
    geoAttempted.current = true;
    doGeolocation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Click outside to close results ──
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowResults(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function doGeolocation() {
    if (!navigator.geolocation) return;
    setGeoLoading(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation(pos.coords.latitude, pos.coords.longitude, "Current location");
        setGeoLoading(false);
      },
      () => {
        // Denied or unavailable — show prompt state, don't re-prompt
        setGeoLoading(false);
        // Focus search input so user can type a location
        inputRef.current?.focus();
      },
      { timeout: 10_000 }
    );
  }

  function selectLocation(result: { name: string; latitude: number; longitude: number }) {
    setLocation(result.latitude, result.longitude, result.name);
    setQuery("");
    setDebouncedQuery("");
    setShowResults(false);
  }

  return (
    <div ref={wrapperRef} className="relative w-full max-w-2xl mx-auto">
      <div className="flex items-center gap-3">
        {/* Current location name */}
        {name && (
          <span className="font-sans text-sm text-ink/70 shrink-0">
            {name}
          </span>
        )}

        {/* Search input */}
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setShowResults(true);
            }}
            onFocus={() => {
              if (debouncedQuery.trim()) setShowResults(true);
            }}
            placeholder={name ? "Search another location" : "Search for a location or enable location access"}
            className="w-full bg-transparent border-b border-hairline px-1 py-2 text-sm font-sans text-ink placeholder:text-ink/40 focus:border-isobar focus:outline-none transition-colors"
          />

          {/* Loading indicator */}
          {isLoading && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-ink/40">
              …
            </span>
          )}

          {/* Search results dropdown */}
          {showResults && data?.results && data.results.length > 0 && (
            <ul className="absolute top-full left-0 right-0 z-10 mt-1 bg-paper border border-hairline">
              {data.results.map((result, i) => (
                <li key={`${result.latitude}-${result.longitude}-${i}`}>
                  <button
                    type="button"
                    onClick={() => selectLocation(result)}
                    className="w-full text-left px-3 py-2 text-sm font-sans text-ink hover:bg-isobar/10 transition-colors border-b border-hairline last:border-b-0"
                  >
                    {result.name}
                    {result.country && (
                      <span className="text-ink/50">, {result.country}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Geolocation button */}
        <button
          type="button"
          onClick={doGeolocation}
          disabled={geoLoading}
          className="shrink-0 text-sm font-sans text-isobar hover:text-isobar/80 disabled:text-ink/30 transition-colors"
          aria-label="Use current location"
        >
          {geoLoading ? (
            <span className="text-xs">Locating…</span>
          ) : (
            <GpsIcon className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Inline error for search */}
      <InlineError error={error} section="search" />
    </div>
  );
}
