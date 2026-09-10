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
3. For travel, driving, logistics, and trucking questions (e.g., from City A to City B, or highway driving):
   - Compare weather conditions at both origin and destination.
   - Evaluate driving risks: rain, wet highways, wind, and fog.
   - Fog risk: High relative humidity (>80%) combined with cool night temperatures and low wind (<2.5 m/s) indicates elevated fog and reduced road visibility. Advise appropriate driving precautions (fog lights, safe distance, transit timing for perishable goods).
4. For agriculture, farming, crop management, and gardening questions (e.g., paddy, crops, terrace/urban gardens, pest/disease control):
   - Correlate humidity, temperature, and rain with plant health and agronomy.
   - High humidity (>70-80%) and persistent rain create high risk for fungal pathogens (e.g., blast, sheath blight, root rot, powdery mildew) and favor certain insect pests.
   - Advise on irrigation (skip watering during rain events) and spray timing (avoid applying pesticides/fertilizers right before rain to prevent runoff).
   - Address both large-scale farm needs and urban garden needs when both locations/contexts are mentioned.
5. Multi-location queries: When data for two locations (origin/destination or city/farm) is provided, address BOTH clearly.

STRICT RULES:
1. Base all numerical weather assessments on the provided JSON blocks (current weather, 7-day forecast, and tonight conditions). Do not fabricate numbers.
2. Provide concise, clear, and highly actionable advice (typically 3-6 sentences).
3. For dangerous weather (storms, flash floods, dense fog), prioritize user and cargo safety.
4. You have access to conversation history — use it to give contextual, coherent responses.
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


def _weather_block(
    weather_data: WeatherData,
    label: str = "Weather data",
    forecast_summary: Optional[list] = None,
    tonight_summary: Optional[dict] = None,
) -> str:
    parts = [
        f"{label} (authoritative — do not modify or supplement):",
        f"```json\n{json.dumps(weather_data.model_dump(exclude_none=True), indent=2)}\n```",
    ]
    if tonight_summary:
        parts.append(
            f"Tonight's conditions for {weather_data.location}:\n"
            f"```json\n{json.dumps(tonight_summary, indent=2)}\n```"
        )
    if forecast_summary:
        parts.append(
            f"7-Day daily forecast for {weather_data.location}:\n"
            f"```json\n{json.dumps(forecast_summary, indent=2)}\n```"
        )
    return "\n\n".join(parts)


def _build_history_messages(history: Optional[List[HistoryMessage]]) -> list:
    """Convert conversation history to Groq message format (last 10 turns max)."""
    if not history:
        return []
    # Take last 10 turns to stay within context limits
    recent = history[-10:]
    return [{"role": m.role, "content": m.content} for m in recent]


def _rule_based_alert_intent(user_message: str) -> Optional[dict]:
    """Fast deterministic check for common alert keywords."""
    msg = user_message.lower().strip()
    alert_keywords = ["alert", "notify", "notification", "warn", "warning", "remind"]
    if not any(kw in msg for kw in alert_keywords):
        return None

    if any(w in msg for w in ["rain", "shower", "downpour", "drizzle"]):
        condition = "rain_probability"
    elif any(w in msg for w in ["temp", "heat", "hot", "cold", "freeze", "warm"]):
        condition = "temperature"
    elif any(w in msg for w in ["wind", "gust", "storm", "cyclone", "breeze", "gale"]):
        condition = "wind_speed"
    elif any(w in msg for w in ["precip", "rainfall", "flood", "mm"]):
        condition = "precipitation"
    else:
        condition = "rain_probability"

    operator = "below" if any(w in msg for w in ["below", "under", "<", "less", "cold", "drop"]) else "above"

    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", msg)
    threshold = float(numbers[-1]) if numbers else 0.0

    return {
        "intent": True,
        "condition": condition,
        "threshold": threshold,
        "operator": operator,
    }


async def _detect_alert_intent(user_message: str) -> Optional[dict]:
    """
    Detect if the message contains alert intent using fast rules first,
    falling back to LLM completion for ambiguous phrasings.
    """
    # 1. Fast deterministic check
    fast_result = _rule_based_alert_intent(user_message)
    if fast_result:
        return fast_result

    # 2. LLM fallback
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
        match = re.search(r"\{.*\}", raw.strip(), re.DOTALL)
        if match:
            data = json.loads(match.group())
            if data.get("intent") is True:
                return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Alert intent detection fallback failed: %s", exc)
    return None


async def generate_weather_response(
    user_question: str,
    weather_data: WeatherData,
    history: Optional[List[HistoryMessage]] = None,
    destination_weather: Optional[WeatherData] = None,
    alert_suggestion: Optional[AlertSuggestion] = None,
    forecast_summary: Optional[list] = None,
    destination_forecast_summary: Optional[list] = None,
    tonight_summary: Optional[dict] = None,
    destination_tonight_summary: Optional[dict] = None,
) -> str:
    """
    Call Groq and return its answer as a plain string.

    Args:
        user_question: The user's current message
        weather_data: Primary location weather data
        history: Conversation history for context
        destination_weather: Destination weather for travel queries
        alert_suggestion: Structured alert suggestion if alert intent was detected
        forecast_summary: 7-day daily forecast summary for primary location
        destination_forecast_summary: 7-day daily forecast summary for destination
        tonight_summary: Tonight's weather/fog/rain summary for primary location
        destination_tonight_summary: Tonight's weather/fog/rain summary for destination
    """
    max_len = settings.max_message_length
    if len(user_question) > max_len:
        logger.warning("User message truncated from %d to %d chars", len(user_question), max_len)
        user_question = user_question[:max_len]

    # Build weather context block(s)
    if destination_weather:
        weather_context = (
            f"{_weather_block(weather_data, 'Origin / Primary weather', forecast_summary, tonight_summary)}\n\n"
            f"{_weather_block(destination_weather, 'Destination / Secondary weather', destination_forecast_summary, destination_tonight_summary)}"
        )
    else:
        weather_context = _weather_block(weather_data, "Primary weather", forecast_summary, tonight_summary)

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
