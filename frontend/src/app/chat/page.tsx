/* ──────────────────────────────────────────────
 * Chat Page — /chat
 * Conversational UI hitting POST /api/v1/chat.
 * Auto-passes GPS coords from location store.
 * Renders weather_data as inline widget.
 * ────────────────────────────────────────────── */

"use client";

import { useState, useRef, useEffect } from "react";
import { useLocationStore } from "@/lib/store";
import { sendChatMessage, getCurrentWeather, ApiError } from "@/lib/api";
import { WeatherWidget } from "@/components/weather-widget";

import { PinIcon } from "@/components/icons";
import type { ChatMessage, WeatherData } from "@/lib/types";

interface DisplayMessage {
  role: "user" | "assistant" | "system";
  content: string;
  weather_data?: WeatherData;
}

const SUGGESTIONS = [
  "What's the weather like right now?",
  "Will it rain today?",
  "Weather in Mumbai",
  "How's the humidity?",
  "Is it windy outside?",
  "7-day forecast for Delhi",
];

export default function ChatPage() {
  const { lat, lng, name: locationName, setLocation } = useLocationStore();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [detectingLoc, setDetectingLoc] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input on mount & auto-detect location if not set
  useEffect(() => {
    inputRef.current?.focus();
    if (lat === null || lng === null) {
      detectLocation();
    }
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
      () => {
        setDetectingLoc(false);
      },
      { timeout: 8000 }
    );
  }


  async function doSend(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    const userMsg: DisplayMessage = { role: "user", content: trimmed };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);

    const history: ChatMessage[] = messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

    try {
      const response = await sendChatMessage({
        message: trimmed,
        lat: lat ?? undefined,
        lng: lng ?? undefined,
        location_name: locationName ?? undefined,
        history,
      });

      // If backend resolved a specific location and user didn't have one, sync it
      if (response.location && response.location !== "Unknown" && response.location !== "Global" && !locationName) {
        setLocation(lat ?? 0, lng ?? 0, response.location);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          weather_data: response.weather_data,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError) {
        const isGuardrail =
          err.code === "WEATHER_GUARDRAIL_TRIGGERED" ||
          err.status === 403 ||
          err.message.toLowerCase().includes("weather-related");

        if (isGuardrail) {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content:
                "I'm WeatherGPT, focused exclusively on weather and atmospheric conditions! You can ask about current conditions, forecasts, precipitation, or temperatures for any city worldwide (e.g. 'Weather in Tokyo' or 'Will it rain today?').",
            },
          ]);
        } else if (err.code === "LOCATION_REQUIRED") {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content:
                "I need a location to check the weather. You can mention a city in your message (e.g. \"weather in London\"), or click 'Detect GPS' below.",
            },
          ]);
        } else {
          setMessages((prev) => [
            ...prev,
            { role: "system", content: err.message },
          ]);
        }
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "system", content: "Something went wrong. Please try again." },
        ]);
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
    // Item 4: flex-col with flex-1 so the messages area fills remaining height
    <main className="flex flex-1 flex-col w-full max-w-2xl mx-auto min-h-0">
      {/* Messages / empty-state area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 min-h-0">
        {isEmpty ? (
          /* ── Empty state (Item 4): fully centered in the available space ── */
          <div className="flex flex-col items-center justify-center h-full gap-6">
            <div className="text-center">
              <h1 className="font-mono text-3xl font-semibold text-isobar">
                WeatherGPT
              </h1>
              <p className="font-sans text-sm text-ink/40 mt-2">
                Ask me about the weather anywhere in the world
              </p>
              {/* Item 1: Replace 📍 emoji with themed SVG PinIcon */}
              {locationName && (
                <p className="font-sans text-xs text-ink/30 mt-1 flex items-center justify-center gap-1">
                  <PinIcon className="w-3 h-3 text-ink/30" />
                  {locationName}
                </p>
              )}
            </div>

            <hr className="w-32 border-t border-hairline" />

            {/* Suggestion chips */}
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
          /* ── Message thread — top-anchored, grows downward ── */
          <div className="space-y-5">
            {messages.map((msg, i) => (
              <div key={i}>
                {msg.role === "user" && (
                  <div className="flex justify-end">
                    <div className="max-w-[80%] px-4 py-2.5 bg-isobar/10 border border-hairline">
                      <p className="font-sans text-sm text-ink leading-relaxed">
                        {msg.content}
                      </p>
                    </div>
                  </div>
                )}

                {msg.role === "assistant" && (
                  <div className="flex justify-start">
                    <div className="max-w-[85%]">
                      <p className="font-sans text-sm text-ink leading-relaxed">
                        {msg.content}
                      </p>
                      {msg.weather_data && (
                        <WeatherWidget data={msg.weather_data} />
                      )}
                    </div>
                  </div>
                )}

                {msg.role === "system" && (
                  <div className="flex justify-center py-1">
                    <p className="font-sans text-xs text-ochre border border-ochre/20 px-3 py-1.5">
                      {msg.content}
                    </p>
                  </div>
                )}
              </div>
            ))}

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

      {/* Input area — always docked at bottom */}
      <div className="border-t border-hairline px-6 py-3 shrink-0">
        <div className="flex items-center justify-between text-xs font-sans text-ink/50 mb-2">
          <div className="flex items-center gap-1.5">
            <PinIcon className="w-3 h-3 text-isobar" />
            {locationName ? (
              <span>
                Location: <strong className="text-ink font-medium">{locationName}</strong>
              </span>
            ) : (
              <span>
                Location: <span className="italic">Not set (auto-detect or mention a city)</span>
              </span>
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
                ? `Ask about weather in ${locationName}…`
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
