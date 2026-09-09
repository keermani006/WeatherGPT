/* ──────────────────────────────────────────────
 * Inline Error — section-scoped error message
 * Maps ApiError codes to user-friendly messages.
 * Never a toast or modal — always inline.
 * ────────────────────────────────────────────── */

"use client";

import { ApiError } from "@/lib/api";

interface InlineErrorProps {
  error: Error | null;
  /** Which section this error appears in */
  section: "weather" | "search" | "forecast" | "hourly";
}

function getMessage(error: Error): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong — retrying shortly.";
  }

  switch (error.code) {
    case "WEATHER_SERVICE_ERROR":
    case "WEATHER_SERVICE_TIMEOUT":
      return "Couldn't reach the weather service — retrying shortly.";

    case "GEOCODING_SERVICE_UNAVAILABLE":
      return "Search is temporarily unavailable.";

    case "INVALID_QUERY":
    case "MISSING_QUERY":
      return ""; // Should never render — search is disabled for empty input

    case "INVALID_FORECAST_DAYS":
    case "INVALID_DATE_FORMAT":
    case "DATE_OUT_OF_RANGE":
      return "Couldn't reach the weather service — retrying shortly.";

    default:
      return error.message || "An unexpected error occurred.";
  }
}

export function InlineError({ error }: InlineErrorProps) {
  if (!error) return null;

  const message = getMessage(error);
  if (!message) return null;

  return (
    <p className="font-sans text-sm text-ochre py-2">
      {message}
    </p>
  );
}
