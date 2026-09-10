/* ──────────────────────────────────────────────
 * Chat Page — /chat
 * Multi-turn persistent chat with WeatherGPT.
 * Features:
 *  - Messages persisted to localStorage (survives refresh)
 *  - Conversation history sent to backend for context-aware replies
 *  - Travel queries show dual-location weather widget
 *  - Alert suggestion card with 1-click alert creation
 * ────────────────────────────────────────────── */

"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Link from "next/link";
import { useLocationStore } from "@/lib/store";
import { sendChatMessage, createAlert, getCurrentWeather, ApiError } from "@/lib/api";
import { WeatherWidget } from "@/components/weather-widget";
import { PinIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth-context";
import type { ChatMessage, WeatherData } from "@/lib/types";

// ── Types ──────────────────────────────────────────────────────────────────

interface AlertSuggestion {
  location_name: string;
  latitude: number;
  longitude: number;
  condition: string;
  threshold: number;
  description: string;
}

interface DisplayMessage {
  role: "user" | "assistant" | "system";
  content: string;
  weather_data?: WeatherData;
  destination_weather?: WeatherData;
  alert_suggestion?: AlertSuggestion;
}

// ── Constants ──────────────────────────────────────────────────────────────

const STORAGE_KEY = "weathergpt_chat_history";
const MAX_STORED_MESSAGES = 40;

const SUGGESTIONS = [
  "What's the weather like right now?",
  "Will it rain today?",
  "Is it okay to travel to Delhi now?",
  "Weather in Mumbai",
  "7-day forecast for Bangalore",
  "Alert me when it rains heavily",
];

const CONDITION_LABELS: Record<string, string> = {
  rain_probability: "Rain Probability",
  temperature: "Temperature",
  wind_speed: "Wind Speed",
  precipitation: "Precipitation",
};

const CONDITION_UNITS: Record<string, string> = {
  rain_probability: "%",
  temperature: "°C",
  wind_speed: " km/h",
  precipitation: " mm",
};

// ── Component ──────────────────────────────────────────────────────────────

export default function ChatPage() {
  const { lat, lng, name: locationName, setLocation } = useLocationStore();
  const { isAuthenticated, loginDemo } = useAuth();

  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [detectingLoc, setDetectingLoc] = useState(false);
  const [alertCreating, setAlertCreating] = useState<string | null>(null); // message index
  const [alertCreated, setAlertCreated] = useState<Set<number>>(new Set());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const initialized = useRef(false);

  // ── Restore messages from localStorage ─────────────────────────────────
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed: DisplayMessage[] = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setMessages(parsed);
        }
      }
    } catch {
      // ignore
    }
  }, []);

  // ── Persist messages to localStorage on change ──────────────────────────
  useEffect(() => {
    if (!initialized.current) return;
    try {
      const toStore = messages.slice(-MAX_STORED_MESSAGES);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(toStore));
    } catch {
      // ignore storage quota errors
    }
  }, [messages]);

  // ── Auto-scroll ─────────────────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── Focus + auto-detect location ────────────────────────────────────────
  useEffect(() => {
    inputRef.current?.focus();
    if (lat === null || lng === null) detectLocation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function detectLocation() {
    if (typeof window === "undefined" || !navigator.geolocation) return;
    setDetectingLoc(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const latitude = pos.coords.latitude;
        const longitude = pos.coords.longitude;
        try {
          const res = await getCurrentWeather({ lat: latitude, lng: longitude });
          setLocation(latitude, longitude, res.location.name);
        } catch {
          setLocation(latitude, longitude, "Current Location");
        } finally {
          setDetectingLoc(false);
        }
      },
      () => { setDetectingLoc(false); },
      { timeout: 8000 }
    );
  }

  // ── Clear chat ──────────────────────────────────────────────────────────
  function clearChat() {
    setMessages([]);
    setAlertCreated(new Set());
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
  }

  // ── Create alert from suggestion ────────────────────────────────────────
  async function handleCreateAlert(suggestion: AlertSuggestion, msgIndex: number) {
    setAlertCreating(String(msgIndex));
    try {
      if (!isAuthenticated) {
        // Seamlessly initialize demo/guest session so alert can be saved
        await loginDemo();
      }
      await createAlert({
        lat: suggestion.latitude,
        lng: suggestion.longitude,
        condition: suggestion.condition,
        threshold: suggestion.threshold,
        location_name: suggestion.location_name,
      });
      setAlertCreated(prev => new Set([...prev, msgIndex]));
      setMessages(prev => [...prev, {
        role: "system",
        content: `✅ Alert active! You'll be notified when ${CONDITION_LABELS[suggestion.condition] || suggestion.condition} ${suggestion.condition === "temperature" ? "goes below" : "exceeds"} ${suggestion.threshold}${CONDITION_UNITS[suggestion.condition] || ""} in ${suggestion.location_name}.`,
      }]);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to create alert. Please try again.";
      setMessages(prev => [...prev, { role: "system", content: `❌ ${msg}` }]);
    } finally {
      setAlertCreating(null);
    }
  }

  // ── Send message ────────────────────────────────────────────────────────
  async function doSend(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    const userMsg: DisplayMessage = { role: "user", content: trimmed };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInput("");
    setSending(true);

    // Build history (only user/assistant roles, last 10 turns)
    const history: ChatMessage[] = updatedMessages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-10)
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

    try {
      const response = await sendChatMessage({
        message: trimmed,
        lat: lat ?? undefined,
        lng: lng ?? undefined,
        location_name: locationName ?? undefined,
        history,
      });

      if (
        response.location &&
        response.location !== "Unknown" &&
        response.location !== "Global" &&
        !locationName
      ) {
        setLocation(lat ?? 0, lng ?? 0, response.location);
      }

      const assistantMsgIndex = updatedMessages.length;
      const createdAlert = (response as any).created_alert;
      const alertSuggestion = (response as any).alert_suggestion;

      const effectiveSuggestion: AlertSuggestion | undefined =
        alertSuggestion ||
        (createdAlert
          ? {
              location_name: createdAlert.location_name || response.location,
              latitude: createdAlert.latitude,
              longitude: createdAlert.longitude,
              condition: createdAlert.condition,
              threshold: createdAlert.threshold,
              description: `Alert when ${CONDITION_LABELS[createdAlert.condition] || createdAlert.condition} reaches threshold in ${createdAlert.location_name || response.location}`,
            }
          : undefined);

      setMessages(prev => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          weather_data: response.weather_data ?? undefined,
          destination_weather: (response as any).destination_weather ?? undefined,
          alert_suggestion: effectiveSuggestion,
        },
      ]);

      // If backend already created the alert:
      if (createdAlert) {
        setAlertCreated(prev => new Set([...prev, assistantMsgIndex]));
        setMessages(prev => [
          ...prev,
          {
            role: "system",
            content: `✅ Alert active! Monitored: ${CONDITION_LABELS[createdAlert.condition] || createdAlert.condition} (threshold: ${createdAlert.threshold}${CONDITION_UNITS[createdAlert.condition] || ""}) in ${createdAlert.location_name || "your location"}.`,
          },
        ]);
      } else if (effectiveSuggestion) {
        // Automatically save alert to backend!
        handleCreateAlert(effectiveSuggestion, assistantMsgIndex);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        const isGuardrail =
          err.code === "WEATHER_GUARDRAIL_TRIGGERED" ||
          err.status === 403 ||
          err.message.toLowerCase().includes("weather-related");

        if (isGuardrail) {
          setMessages(prev => [...prev, {
            role: "assistant",
            content: "I'm WeatherGPT, focused on weather! Ask about current conditions, forecasts, precipitation, or temperatures for any city (e.g. 'Weather in Tokyo' or 'Will it rain today?').",
          }]);
        } else if (err.code === "LOCATION_REQUIRED") {
          setMessages(prev => [...prev, {
            role: "assistant",
            content: "I need a location to check the weather. Mention a city (e.g. 'weather in London'), or click 'Detect GPS'.",
          }]);
        } else {
          setMessages(prev => [...prev, { role: "system", content: err.message }]);
        }
      } else {
        setMessages(prev => [...prev, { role: "system", content: "Something went wrong. Please try again." }]);
      }
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend(input);
    }
  }

  const isEmpty = messages.length === 0;

  return (
    <main className="flex flex-1 flex-col w-full max-w-2xl mx-auto min-h-0">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 min-h-0">
        {isEmpty ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full gap-6">
            <div className="text-center">
              <h1 className="font-mono text-3xl font-semibold text-isobar">WeatherGPT</h1>
              <p className="font-sans text-sm text-ink/40 mt-2">
                Ask about weather, travel safety, or set alerts
              </p>
              {locationName && (
                <p className="font-sans text-xs text-ink/30 mt-1 flex items-center justify-center gap-1">
                  <PinIcon className="w-3 h-3 text-ink/30" />
                  {locationName}
                </p>
              )}
            </div>

            <hr className="w-32 border-t border-hairline" />

            <div className="flex flex-wrap justify-center gap-2 max-w-md">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => doSend(suggestion)}
                  disabled={sending}
                  className="px-3 py-1.5 text-xs font-sans text-isobar border border-hairline hover:border-isobar/40 hover:bg-isobar/5 transition-colors disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Message thread */
          <div className="space-y-5">
            {/* Clear chat button */}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={clearChat}
                className="font-sans text-[11px] text-ink/30 hover:text-ochre transition-colors"
              >
                Clear chat
              </button>
            </div>

            {messages.map((msg, i) => (
              <div key={i}>
                {/* User message */}
                {msg.role === "user" && (
                  <div className="flex justify-end">
                    <div className="max-w-[80%] px-4 py-2.5 bg-isobar/10 border border-hairline">
                      <p className="font-sans text-sm text-ink leading-relaxed">{msg.content}</p>
                    </div>
                  </div>
                )}

                {/* Assistant message */}
                {msg.role === "assistant" && (
                  <div className="flex justify-start">
                    <div className="max-w-[90%] space-y-3">
                      <p className="font-sans text-sm text-ink leading-relaxed">{msg.content}</p>

                      {/* Primary weather widget */}
                      {msg.weather_data && <WeatherWidget data={msg.weather_data} />}

                      {/* Destination weather widget (travel queries) */}
                      {msg.destination_weather && (
                        <div className="space-y-1">
                          <span className="font-mono text-[10px] uppercase tracking-widest text-ink/40">
                            Destination
                          </span>
                          <WeatherWidget data={msg.destination_weather} />
                        </div>
                      )}

                      {/* Alert suggestion / active card */}
                      {msg.alert_suggestion && (
                        <div className={`border px-4 py-3 space-y-2 transition-colors ${
                          alertCreated.has(i)
                            ? "border-teal/60 bg-teal/10"
                            : "border-teal/30 bg-teal/5"
                        }`}>
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <span className="font-mono text-[10px] uppercase tracking-widest text-teal font-semibold flex items-center gap-1.5">
                                {alertCreated.has(i) ? "✓ Alert Active" : "Set Alert"}
                              </span>
                              <p className="font-sans text-xs text-ink/80 mt-0.5 font-medium">
                                {msg.alert_suggestion.description}
                              </p>
                            </div>
                            {alertCreated.has(i) ? (
                              <Link
                                href="/alerts"
                                className="shrink-0 px-3 py-1.5 text-xs font-sans font-medium bg-teal/20 text-teal hover:bg-teal/30 transition-colors flex items-center gap-1"
                              >
                                View Alerts →
                              </Link>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleCreateAlert(msg.alert_suggestion!, i)}
                                disabled={alertCreating === String(i)}
                                className="shrink-0 px-3 py-1.5 text-xs font-sans font-medium bg-teal text-paper hover:bg-teal/90 disabled:bg-ink/20 transition-colors flex items-center gap-1.5"
                              >
                                {alertCreating === String(i) ? (
                                  <>
                                    <span className="w-3 h-3 border-2 border-paper/30 border-t-paper rounded-full animate-spin" />
                                    <span>Creating…</span>
                                  </>
                                ) : (
                                  "🔔 Create Alert"
                                )}
                              </button>
                            )}
                          </div>
                          <div className="flex items-center gap-3 text-[11px] font-mono text-ink/50">
                            <span>{CONDITION_LABELS[msg.alert_suggestion.condition] || msg.alert_suggestion.condition}</span>
                            <span>·</span>
                            <span>Threshold: {msg.alert_suggestion.threshold}{CONDITION_UNITS[msg.alert_suggestion.condition] || ""}</span>
                            <span>·</span>
                            <span>{msg.alert_suggestion.location_name}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* System message */}
                {msg.role === "system" && (
                  <div className="flex justify-center py-1">
                    <p className="font-sans text-xs text-ochre border border-ochre/20 px-3 py-1.5">
                      {msg.content}
                    </p>
                  </div>
                )}
              </div>
            ))}

            {/* Typing indicator */}
            {sending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-1 py-2">
                  <span className="w-1.5 h-1.5 bg-isobar/40 rounded-full animate-pulse" />
                  <span className="w-1.5 h-1.5 bg-isobar/40 rounded-full animate-pulse [animation-delay:150ms]" />
                  <span className="w-1.5 h-1.5 bg-isobar/40 rounded-full animate-pulse [animation-delay:300ms]" />
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-hairline px-6 py-3 shrink-0">
        <div className="flex items-center justify-between text-xs font-sans text-ink/50 mb-2">
          <div className="flex items-center gap-1.5">
            <PinIcon className="w-3 h-3 text-isobar" />
            {locationName ? (
              <span>Location: <strong className="text-ink font-medium">{locationName}</strong></span>
            ) : (
              <span>Location: <span className="italic">Not set (auto-detect or mention a city)</span></span>
            )}
          </div>
          <button
            type="button"
            onClick={detectLocation}
            disabled={detectingLoc}
            className="text-[11px] text-isobar hover:underline flex items-center gap-1 disabled:opacity-50"
          >
            {detectingLoc ? "Detecting GPS…" : locationName ? "↻ Re-detect GPS" : "📍 Detect GPS"}
          </button>
        </div>

        <div className="flex items-end gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              locationName
                ? `Ask about weather, travel to another city, or set an alert…`
                : "Ask about weather in any city (e.g. London)…"
            }
            rows={1}
            className="flex-1 resize-none bg-transparent border-b border-hairline px-1 py-2 text-sm font-sans text-ink placeholder:text-ink/40 focus:border-isobar focus:outline-none transition-colors"
          />
          <button
            type="button"
            onClick={() => doSend(input)}
            disabled={!input.trim() || sending}
            className="shrink-0 px-4 py-2 text-sm font-sans text-paper bg-isobar disabled:bg-ink/20 transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </main>
  );
}
