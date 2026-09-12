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
import { RouteItineraryCard } from "@/components/route-itinerary-card";
import { PinIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth-context";
import type { ChatMessage, RouteWaypoint, TravelCardData, WeatherData } from "@/lib/types";

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
  route_waypoints?: RouteWaypoint[];
  travel_card?: TravelCardData;
  alert_suggestion?: AlertSuggestion;
}

// ── Constants ──────────────────────────────────────────────────────────────

const STORAGE_KEY = "weathergpt_chat_history";
const MAX_STORED_MESSAGES = 40;

function getChatStorageKey(userId?: string | null): string {
  if (userId) {
    return `weathergpt_chat_history_${userId}`;
  }
  return "weathergpt_chat_history_guest";
}

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
  const { user, isLoading: isAuthLoading, isAuthenticated, loginDemo } = useAuth();

  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [detectingLoc, setDetectingLoc] = useState(false);
  const [alertCreating, setAlertCreating] = useState<string | null>(null); // message index
  const [alertCreated, setAlertCreated] = useState<Set<number>>(new Set());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const currentLoadedUserRef = useRef<string | null | undefined>(undefined);

  // ── Restore messages from localStorage scoped to active user ────────────
  useEffect(() => {
    if (isAuthLoading) return;

    const currentUserId = user?.id ?? null;
    if (currentLoadedUserRef.current === currentUserId) return;

    currentLoadedUserRef.current = currentUserId;
    const storageKey = getChatStorageKey(currentUserId);

    // Clean legacy un-scoped chat history key so old messages do not bleed into accounts
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }

    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const parsed: DisplayMessage[] = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          setMessages(parsed);
          setAlertCreated(new Set());
          return;
        }
      }
    } catch {
      // ignore corrupt storage
    }

    setMessages([]);
    setAlertCreated(new Set());
  }, [user?.id, isAuthLoading]);

  // ── Persist messages to localStorage for current active user ─────────────
  useEffect(() => {
    if (isAuthLoading) return;
    const currentUserId = user?.id ?? null;
    // Do not save before user identity has finished loading/restoring
    if (currentLoadedUserRef.current !== currentUserId) return;

    const storageKey = getChatStorageKey(currentUserId);
    try {
      const toStore = messages.slice(-MAX_STORED_MESSAGES);
      if (toStore.length > 0) {
        localStorage.setItem(storageKey, JSON.stringify(toStore));
      } else {
        localStorage.removeItem(storageKey);
      }
    } catch {
      // ignore storage quota errors
    }
  }, [messages, user?.id, isAuthLoading]);

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
    const storageKey = getChatStorageKey(user?.id);
    try {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
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
          destination_weather: response.destination_weather ?? undefined,
          route_waypoints: response.route_waypoints ?? undefined,
          travel_card: response.travel_card ?? undefined,
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
    <main className="flex flex-1 flex-col w-full max-w-2xl md:max-w-3xl mx-auto min-h-0">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-3.5 sm:px-6 py-4 sm:py-6 min-h-0">
        {isEmpty ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full gap-5 sm:gap-6 px-2">
            <div className="text-center">
              <h1 className="font-mono text-2xl sm:text-3xl font-semibold text-isobar">WeatherGPT</h1>
              <p className="font-sans text-xs sm:text-sm text-ink/50 mt-1.5 sm:mt-2">
                Ask about weather, travel safety, or set alerts
              </p>
              {locationName && (
                <p className="font-sans text-xs text-ink/40 mt-1 flex items-center justify-center gap-1">
                  <PinIcon className="w-3 h-3 text-ink/30" />
                  <span>{locationName}</span>
                </p>
              )}
            </div>

            <hr className="w-24 sm:w-32 border-t border-hairline" />

            <div className="flex flex-wrap justify-center gap-1.5 sm:gap-2 max-w-md">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => doSend(suggestion)}
                  disabled={sending}
                  className="px-2.5 sm:px-3 py-1.5 text-xs font-sans text-isobar border border-hairline hover:border-isobar/40 hover:bg-isobar/5 transition-colors disabled:opacity-50 rounded-sm cursor-pointer"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Message thread */
          <div className="space-y-4 sm:space-y-5">
            {/* Clear chat button */}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={clearChat}
                className="font-sans text-[11px] text-ink/30 hover:text-ochre transition-colors cursor-pointer"
              >
                Clear chat
              </button>
            </div>

            {messages.map((msg, i) => (
              <div key={i}>
                {/* User message */}
                {msg.role === "user" && (
                  <div className="flex justify-end">
                    <div className="max-w-[88%] sm:max-w-[80%] px-3.5 sm:px-4 py-2 sm:py-2.5 bg-isobar/10 border border-hairline rounded-sm">
                      <p className="font-sans text-sm text-ink leading-relaxed break-words">{msg.content}</p>
                    </div>
                  </div>
                )}

                {/* Assistant message */}
                {msg.role === "assistant" && (
                  <div className="flex justify-start">
                    <div className="w-full sm:max-w-[92%] space-y-3">
                      <p className="font-sans text-sm text-ink leading-relaxed break-words">{msg.content}</p>

                      {/* Primary weather widget (only if not a route travel query) */}
                      {!msg.travel_card && msg.weather_data && <WeatherWidget data={msg.weather_data} />}

                      {/* Travel Weather Card (route/transit queries) */}
                      {msg.travel_card ? (
                        <RouteItineraryCard
                          travelCard={msg.travel_card}
                          originName={msg.weather_data?.location || "Origin"}
                          originWeather={msg.weather_data}
                          destinationName={msg.destination_weather?.location || "Destination"}
                          destinationWeather={msg.destination_weather}
                          waypoints={msg.route_waypoints || []}
                        />
                      ) : msg.route_waypoints && msg.route_waypoints.length > 0 ? (
                        <RouteItineraryCard
                          originName={msg.weather_data?.location || "Origin"}
                          originWeather={msg.weather_data}
                          destinationName={msg.destination_weather?.location || "Destination"}
                          destinationWeather={msg.destination_weather}
                          waypoints={msg.route_waypoints}
                        />
                      ) : (
                        msg.destination_weather && (
                          <div className="space-y-1">
                            <span className="font-mono text-[10px] uppercase tracking-widest text-ink/40">
                              Destination
                            </span>
                            <WeatherWidget data={msg.destination_weather} />
                          </div>
                        )
                      )}

                      {/* Alert suggestion / active card */}
                      {msg.alert_suggestion && (
                        <div className="border border-hairline bg-paper/60 p-3 sm:p-3.5 space-y-2 mt-2">
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-[10px] uppercase tracking-widest text-ochre font-semibold">
                              Suggested Alert
                            </span>
                            {alertCreated.has(i) && (
                              <span className="font-mono text-[10px] text-teal font-medium">
                                Active ✓
                              </span>
                            )}
                          </div>

                          <p className="font-sans text-xs text-ink/70">
                            {msg.alert_suggestion.description}
                          </p>

                          <div className="flex items-center justify-between pt-1">
                            <span className="font-mono text-[11px] text-ink/40">
                              {msg.alert_suggestion.location_name} · {CONDITION_LABELS[msg.alert_suggestion.condition] || msg.alert_suggestion.condition} {msg.alert_suggestion.threshold}{CONDITION_UNITS[msg.alert_suggestion.condition] || ""}
                            </span>

                            {alertCreated.has(i) ? (
                              <span className="text-[11px] font-sans text-teal">
                                Alert set
                              </span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleCreateAlert(msg.alert_suggestion!, i)}
                                disabled={alertCreating === String(i)}
                                className="px-3 py-1 text-xs font-sans text-paper bg-isobar hover:bg-isobar/90 disabled:opacity-50 transition-colors cursor-pointer rounded-xs"
                              >
                                {alertCreating === String(i) ? "Setting alert…" : "Set alert"}
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* System message */}
                {msg.role === "system" && (
                  <div className="text-center my-2">
                    <span className="font-sans text-xs text-ink/60 bg-paper/80 border border-hairline px-3 py-1 inline-block">
                      {msg.content}
                    </span>
                  </div>
                )}
              </div>
            ))}

            {/* Sending indicator */}
            {sending && (
              <div className="flex justify-start">
                <div className="flex items-center gap-1 px-3 py-2 text-ink/30 font-mono text-xs">
                  <span>Thinking</span>
                  <span className="animate-pulse">.</span>
                  <span className="animate-pulse delay-100">.</span>
                  <span className="animate-pulse delay-200">.</span>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-hairline px-3.5 sm:px-6 py-2.5 sm:py-3.5 bg-paper shrink-0">
        <div className="flex items-center justify-between text-xs font-sans text-ink/50 mb-2">
          <div className="flex items-center gap-1.5 truncate max-w-[70%] sm:max-w-none">
            <PinIcon className="w-3 h-3 text-isobar shrink-0" />
            {locationName ? (
              <span className="truncate">Location: <strong className="text-ink font-medium">{locationName}</strong></span>
            ) : (
              <span className="italic truncate">Location not set (auto-detect or mention a city)</span>
            )}
          </div>
          <button
            type="button"
            onClick={detectLocation}
            disabled={detectingLoc}
            className="text-[11px] text-isobar hover:underline flex items-center gap-1 disabled:opacity-50 shrink-0 cursor-pointer"
          >
            {detectingLoc ? "Detecting…" : locationName ? "↻ Re-detect GPS" : "📍 Detect GPS"}
          </button>
        </div>

        <div className="flex items-center gap-2 sm:gap-3 bg-paper border border-hairline rounded-lg px-2.5 sm:px-3 py-1.5 focus-within:border-isobar transition-colors">
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
            className="flex-1 resize-none bg-transparent border-0 outline-none ring-0 shadow-none focus:outline-none focus:ring-0 focus:border-0 px-1 py-1 text-base sm:text-sm font-sans text-ink placeholder:text-ink/40 transition-colors"
          />
          <button
            type="button"
            onClick={() => doSend(input)}
            disabled={!input.trim() || sending}
            className="shrink-0 px-3.5 sm:px-4 py-1.5 text-xs sm:text-sm font-sans font-medium text-paper bg-isobar disabled:bg-ink/20 rounded transition-colors cursor-pointer"
          >
            Send
          </button>
        </div>
      </div>
    </main>
  );
}
