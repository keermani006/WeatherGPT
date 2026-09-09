/* ──────────────────────────────────────────────
 * Alerts Page — /alerts
 * Create, list, evaluate, and delete weather alerts.
 * ────────────────────────────────────────────── */

"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocationStore } from "@/lib/store";
import {
  getAlerts,
  createAlert,
  deleteAlert,
  evaluateAlerts,
  ApiError,
} from "@/lib/api";
import { InlineError } from "@/components/inline-error";
import type { Alert } from "@/lib/types";

const CONDITIONS: { value: string; label: string; unit: string }[] = [
  { value: "temperature", label: "Temperature above", unit: "°C" },
  { value: "rain_probability", label: "Rain probability above", unit: "%" },
  { value: "wind_speed", label: "Wind speed above", unit: "km/h" },
  { value: "precipitation", label: "Precipitation above", unit: "mm" },
];

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const { lat, lng, name: locationName } = useLocationStore();

  // Form state
  const [condition, setCondition] = useState("temperature");
  const [threshold, setThreshold] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  // Fetch alerts list
  const {
    data: alertsData,
    error: listError,
    isLoading,
  } = useQuery({
    queryKey: ["alerts"],
    queryFn: getAlerts,
  });

  // Create mutation
  const createMutation = useMutation({
    mutationFn: createAlert,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      setThreshold("");
      setFormError(null);
    },
    onError: (err: Error) => {
      if (err instanceof ApiError) {
        setFormError(err.message);
      } else {
        setFormError("Failed to create alert");
      }
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAlert({ id }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: () => {
      // ALERT_NOT_FOUND → quietly refresh the list
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  // Evaluate mutation
  const evaluateMutation = useMutation({
    mutationFn: evaluateAlerts,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  function handleCreate() {
    setFormError(null);
    const value = parseFloat(threshold);
    if (isNaN(value)) {
      setFormError("Please enter a valid number");
      return;
    }

    if (lat === null || lng === null) {
      setFormError("Set a location first — use the dashboard to search or enable geolocation");
      return;
    }

    createMutation.mutate({
      condition,
      threshold: value,
      lat,
      lng,
      location_name: locationName ?? undefined,
    });
  }

  const alerts = alertsData?.alerts ?? [];
  const selectedUnit = CONDITIONS.find((c) => c.value === condition)?.unit ?? "";

  return (
    <main className="flex-1 overflow-y-auto w-full max-w-2xl mx-auto px-6 py-8">
      <h1 className="font-sans text-2xl font-semibold text-ink">Alerts</h1>
      <p className="font-sans text-sm text-ink/50 mt-1">
        Get notified when weather conditions cross your thresholds
      </p>

      <hr className="border-t border-hairline mt-4 mb-6" />

      {/* ── Create alert form ── */}
      <section>
        <h2 className="font-sans text-sm text-ink/50 mb-3">New alert</h2>

        <div className="flex flex-wrap items-end gap-3">
          {/* Condition dropdown */}
          <div className="flex-1 min-w-[200px]">
            <select
              value={condition}
              onChange={(e) => setCondition(e.target.value)}
              className="w-full bg-transparent border-b border-hairline px-1 py-2 text-sm font-sans text-ink focus:border-isobar focus:outline-none transition-colors"
            >
              {CONDITIONS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          {/* Threshold input */}
          <div className="w-32">
            <div className="flex items-baseline gap-1">
              <input
                type="number"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                placeholder="Value"
                className="w-full bg-transparent border-b border-hairline px-1 py-2 text-sm font-mono text-ink placeholder:text-ink/40 focus:border-isobar focus:outline-none transition-colors"
              />
              <span className="font-mono text-xs text-ink/40">{selectedUnit}</span>
            </div>
          </div>

          {/* Create button */}
          <button
            type="button"
            onClick={handleCreate}
            disabled={createMutation.isPending}
            className="px-4 py-2 text-sm font-sans text-paper bg-isobar disabled:bg-ink/20 transition-colors"
          >
            {createMutation.isPending ? "Creating…" : "Create"}
          </button>
        </div>

        {locationName && (
          <p className="font-sans text-xs text-ink/40 mt-2">
            Location: {locationName}
          </p>
        )}

        {/* Form validation error — inline under inputs */}
        {formError && (
          <p className="font-sans text-sm text-ochre mt-2">{formError}</p>
        )}
      </section>

      <hr className="border-t border-hairline mt-6 mb-4" />

      {/* ── Alerts list ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-sans text-sm text-ink/50">
            Your alerts {alerts.length > 0 && `(${alerts.length})`}
          </h2>

          {alerts.length > 0 && (
            <button
              type="button"
              onClick={() => evaluateMutation.mutate()}
              disabled={evaluateMutation.isPending}
              className="text-xs font-sans text-isobar hover:text-isobar/80 disabled:text-ink/30 transition-colors"
            >
              {evaluateMutation.isPending ? "Checking…" : "Check now"}
            </button>
          )}
        </div>

        {/* List error */}
        <InlineError error={listError} section="weather" />

        {/* Loading */}
        {isLoading && (
          <p className="font-sans text-sm text-ink/30 py-4">Loading alerts…</p>
        )}

        {/* Empty state */}
        {!isLoading && alerts.length === 0 && (
          <p className="font-sans text-sm text-ink/30 py-4">
            No alerts yet — create one above
          </p>
        )}

        {/* Alert rows */}
        <ul role="list">
          {alerts.map((alert: Alert, i: number) => (
            <li
              key={alert.id}
              className={`flex items-center gap-3 py-3 ${
                i > 0 ? "border-t border-hairline" : ""
              }`}
            >
              {/* Triggered indicator — left edge color stripe */}
              <div
                className={`w-1 self-stretch shrink-0 ${
                  alert.triggered ? "bg-ochre" : "bg-hairline"
                }`}
              />

              {/* Alert details */}
              <div className="flex-1 min-w-0">
                <p className="font-sans text-sm text-ink">
                  {formatCondition(alert.condition)}{" "}
                  <span className="font-mono">{alert.threshold}{conditionUnit(alert.condition)}</span>
                </p>

                <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-0.5">
                  <span className="font-sans text-xs text-ink/40">
                    {displayLocation(alert)}
                  </span>

                  {alert.current_value != null && (
                    <span className="font-sans text-xs text-ink/40">
                      Current:{" "}
                      <span className="font-mono">
                        {alert.current_value}{conditionUnit(alert.condition)}
                      </span>
                    </span>
                  )}

                  {alert.evaluated_at && (
                    <span className="font-sans text-xs text-ink/30">
                      Checked{" "}
                      {new Date(alert.evaluated_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  )}
                </div>
              </div>

              {/* Delete button */}
              <button
                type="button"
                onClick={() => deleteMutation.mutate(alert.id)}
                disabled={deleteMutation.isPending}
                className="shrink-0 text-xs font-sans text-ink/30 hover:text-ochre transition-colors"
                aria-label={`Delete alert: ${formatCondition(alert.condition)} ${alert.threshold}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

/** Item 3 — Defensive location display.
 *  Falls back to coordinates when location_name is:
 *  - null / undefined
 *  - empty string
 *  - the Swagger auto-fill placeholder "string" (FastAPI /docs test data)
 *
 *  NOTE: If you see "string" appearing in your alerts list it was created
 *  via the backend's /docs Swagger UI which defaults text fields to "string".
 *  Delete that test alert and verify the backend is not persisting stale data.
 */
function displayLocation(alert: { location_name: string | null; latitude: number; longitude: number }): string {
  const name = alert.location_name;
  if (!name || name.trim() === "" || name.toLowerCase() === "string") {
    return `${alert.latitude.toFixed(2)}, ${alert.longitude.toFixed(2)}`;
  }
  return name;
}

function formatCondition(condition: string): string {
  const map: Record<string, string> = {
    temperature: "Temperature",
    rain_probability: "Rain probability",
    wind_speed: "Wind speed",
    precipitation: "Precipitation",
  };
  return map[condition] ?? condition.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function conditionUnit(condition: string): string {
  if (condition === "temperature") return "°";
  if (condition === "rain_probability") return "%";
  if (condition === "wind_speed") return " km/h";
  if (condition === "precipitation") return " mm";
  return "";
}
