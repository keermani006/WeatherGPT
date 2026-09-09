"""
app/services/llm_service.py

Turn structured WeatherData + a user question into a natural-language answer
using Groq's AsyncGroq client.

Features:
  - Conversation history passed to Groq for context-aware multi-turn chat
  - Travel / multi-location support (origin + destination weather)
  - Alert intent detection: returns structured alert_suggestion when user
    wants to be notified (e.g. "alert me when rain > 70% in Delhi")
  - Singleton AsyncGroq client
  - Circuit breaker on Groq
"""

import json
import logging
import re
from typing import List, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.resilience import cb_groq
from app.schemas.chat import AlertSuggestion, HistoryMessage, WeatherData

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Singleton Groq client ─────────────────────────────────────────────────────
_groq_client: AsyncGroq | None = None


def _get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(
            api_key=settings.groq_api_key,
            timeout=float(settings.llm_timeout),
        )
    return _groq_client


_SYSTEM_PROMPT = """\
You are WeatherGPT, an intelligent conversational weather assistant integrated into the WeatherGPT application.

Your capabilities:
1. Answer weather questions using the structured weather data supplied in the JSON block(s).
2. Assist with meteorological alerts. WeatherGPT has a built-in alert system that lets users create automated threshold alerts for rain, temperature, wind, and precipitation. When a user asks to set, add, create, or notify them about weather conditions (e.g. "add an alert if it rains", "notify me if wind > 50"), ALWAYS confirm that you have prepared an alert card for them directly below your response so they can activate it with one click. NEVER say "I cannot set alerts" or "I cannot send notifications" — WeatherGPT provides the interactive alert card right in this interface!
3. For travel questions (from A to B): compare weather at both locations and give a clear travel recommendation.

STRICT RULES:
1. Use ONLY the weather data in the JSON block(s) provided. Do not invent or hallucinate values.
2. If data is insufficient, say so clearly. Do not guess.
3. Keep answers concise, friendly, and clear. Target 2-4 sentences.
4. For dangerous conditions (extreme heat, storms, flooding), advise users to stay safe and follow official warnings.
5. Use probabilistic language for future weather ("there is a high chance of showers", "rain is likely").
6. You have access to conversation history — use it to give contextual, coherent responses.
"""

_ALERT_DETECTION_PROMPT = """\
Given the user's message, determine if they want to set a weather alert/notification.

Examples of alert intent:
- "alert me when it rains in Delhi"
- "notify me if wind speed exceeds 50 km/h"
- "set an alert for temperature below 5°C"
- "let me know when rain probability is above 70%"

If alert intent detected, respond with ONLY this JSON (no other text):
{"intent": true, "condition": "<rain_probability|temperature|wind_speed|precipitation>", "threshold": <number>, "operator": "<above|below>"}

Condition mapping:
- rain / precipitation probability → "rain_probability" (threshold: 0-100 percent)
- temperature → "temperature" (threshold: degrees Celsius)
- wind → "wind_speed" (threshold: km/h)
- precipitation/rainfall amount → "precipitation" (threshold: mm)

If NO alert intent, respond with ONLY: {"intent": false}
"""


def _weather_block(weather_data: WeatherData, label: str = "Weather data") -> str:
    return (
        f"{label} (authoritative — do not modify or supplement):\n"
        f"```json\n{json.dumps(weather_data.model_dump(exclude_none=True), indent=2)}\n```"
    )


def _build_history_messages(history: Optional[List[HistoryMessage]]) -> list:
    """Convert conversation history to Groq message format (last 10 turns max)."""
    if not history:
        return []
    # Take last 10 turns to stay within context limits
    recent = history[-10:]
    return [{"role": m.role, "content": m.content} for m in recent]


async def _detect_alert_intent(user_message: str) -> Optional[dict]:
    """
    Ask LLM if the message contains alert intent.
    Returns dict with intent/condition/threshold or None.
    """
    client = _get_groq_client()
    try:
        completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": _ALERT_DETECTION_PROMPT},
                {"role": "user", "content": user_message},
            ],
            model=settings.groq_model,
            temperature=0.0,
            max_tokens=100,
        )
        raw = completion.choices[0].message.content or ""
        # Extract JSON from response
        match = re.search(r'\{.*\}', raw.strip(), re.DOTALL)
        if match:
            data = json.loads(match.group())
            if data.get("intent") is True:
                return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Alert intent detection failed: %s", exc)
    return None


async def generate_weather_response(
    user_question: str,
    weather_data: WeatherData,
    history: Optional[List[HistoryMessage]] = None,
    destination_weather: Optional[WeatherData] = None,
    alert_suggestion: Optional[AlertSuggestion] = None,
) -> str:
    """
    Call Groq and return its answer as a plain string.

    Args:
        user_question: The user's current message
        weather_data: Primary location weather data
        history: Conversation history for context
        destination_weather: Destination weather for travel queries
        alert_suggestion: Structured alert suggestion if alert intent was detected
    """
    max_len = settings.max_message_length
    if len(user_question) > max_len:
        logger.warning("User message truncated from %d to %d chars", len(user_question), max_len)
        user_question = user_question[:max_len]

    # Build weather context block(s)
    if destination_weather:
        weather_context = (
            f"{_weather_block(weather_data, 'Origin weather')}\n\n"
            f"{_weather_block(destination_weather, 'Destination weather')}"
        )
    else:
        weather_context = _weather_block(weather_data)

    alert_note = ""
    if alert_suggestion:
        alert_note = (
            f"\n\n[System Note: An interactive alert card has been prepared for the user directly below your response "
            f"(Condition: {alert_suggestion.condition}, Threshold: {alert_suggestion.threshold}, Location: {alert_suggestion.location_name}). "
            f"Confirm you have prepared the alert card below for them to review and create with one click, and summarize current weather.]"
        )

    user_content = f"{weather_context}{alert_note}\n\nUser question: {user_question}"

    client = _get_groq_client()
    history_messages = _build_history_messages(history)

    logger.info(
        "Sending to Groq (model=%s, history_turns=%d, travel=%s)",
        settings.groq_model,
        len(history_messages),
        destination_weather is not None,
    )

    async with cb_groq:
        chat_completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                *history_messages,
                {"role": "user", "content": user_content},
            ],
            model=settings.groq_model,
            temperature=0.3,
            max_tokens=settings.llm_max_output_tokens,
        )

    try:
        content = chat_completion.choices[0].message.content
        answer = content.strip() if content else ""
    except (KeyError, IndexError, AttributeError) as exc:
        raise RuntimeError(f"Unexpected Groq response structure: {exc}") from exc

    logger.info("Groq LLM response received successfully")
    return answer


async def build_alert_suggestion(
    user_message: str,
    weather_data: WeatherData,
    latitude: float = 0.0,
    longitude: float = 0.0,
) -> Optional[AlertSuggestion]:
    """
    Detect alert intent from user message and build a pre-filled AlertSuggestion.
    Returns None if no alert intent found.
    """
    intent_data = await _detect_alert_intent(user_message)
    if not intent_data:
        return None

    raw_cond = str(intent_data.get("condition", "rain_probability")).lower().strip()
    if "rain" in raw_cond or "prob" in raw_cond:
        condition = "rain_probability"
    elif "temp" in raw_cond or "heat" in raw_cond or "cold" in raw_cond:
        condition = "temperature"
    elif "wind" in raw_cond or "storm" in raw_cond or "gust" in raw_cond:
        condition = "wind_speed"
    elif "precip" in raw_cond or "flood" in raw_cond:
        condition = "precipitation"
    else:
        condition = "rain_probability"

    try:
        threshold = float(intent_data.get("threshold", 0))
    except (ValueError, TypeError):
        threshold = 0.0

    # Map condition to a sensible default threshold if LLM returned 0 or invalid value
    if condition == "rain_probability":
        if threshold <= 0 or threshold > 100:
            threshold = 70.0
    elif condition == "temperature":
        if threshold == 0 or threshold < -100 or threshold > 70:
            threshold = 35.0
    elif condition == "wind_speed":
        if threshold <= 0:
            threshold = 40.0
    elif condition == "precipitation":
        if threshold <= 0:
            threshold = 10.0

    description_map = {
        "rain_probability": f"Alert when rain probability > {threshold:.0f}% in {weather_data.location}",
        "temperature": f"Alert when temperature {'<' if intent_data.get('operator') == 'below' else '>'} {threshold:.0f}°C in {weather_data.location}",
        "wind_speed": f"Alert when wind speed > {threshold:.0f} km/h in {weather_data.location}",
        "precipitation": f"Alert when rainfall > {threshold:.0f} mm in {weather_data.location}",
    }

    return AlertSuggestion(
        location_name=weather_data.location,
        latitude=latitude,
        longitude=longitude,
        condition=condition,
        threshold=threshold,
        description=description_map.get(condition, f"Alert for {condition} in {weather_data.location}"),
    )
