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
from app.schemas.chat import (
    AlertSuggestion,
    HistoryMessage,
    RouteWaypoint,
    StructuredConversationState,
    TravelCardData,
    WeatherData,
)

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
3. For travel, route weather, cargo transport, and logistics queries:
   - When the user describes taking goods/crops from one location to another, transporting produce, moving equipment, or visiting another place, treat it as a journey/route weather query.
   - Do NOT answer only with destination weather or only with origin weather. Analyze the weather along the corridor connecting origin and destination, including intermediate pass-by waypoints.
   - If intermediate route waypoints are provided in the data, describe the conditions along the route and highlight where rain or bad weather is expected.
   - Cargo-specific risk: If user mentions cargo (harvested rice, grain, vegetables, produce, cotton, equipment), incorporate practical protection advice. For instance, for harvested rice/grain, emphasize that the critical issue is preventing rainwater from penetrating sacks to avoid grain swelling, mold, and spoilage; advise securing heavy-duty waterproof tarpaulins and elevating sacks off the truck bed.
   - Timing: If the user specified a departure time/date, evaluate that window. If NO departure time was provided, use the upcoming forecast and state that risk depends on departure timing — NEVER invent or assume a departure time.
   - Never fabricate route weather. Do not make unsupported claims such as "it always rains in the afternoon" or "leave early morning to avoid rain" unless the data explicitly supports it.
4. For agriculture, farming, crop management, and gardening questions (e.g., paddy, crops, terrace/urban gardens, pest/disease control):
   - Correlate humidity, temperature, and rain with plant health and agronomy.
   - High humidity (>70-80%) and persistent rain create high risk for fungal pathogens (e.g., blast, sheath blight, root rot, powdery mildew) and favor certain insect pests.
   - Advise on irrigation (skip watering during rain events) and spray timing (avoid applying pesticides/fertilizers right before rain to prevent runoff).
5. Multi-location queries: When data for two locations (origin/destination or city/farm) is provided, address BOTH clearly.

STRICT RULES:
1. Base all numerical weather assessments on the provided JSON blocks (current weather, 7-day forecast, tonight conditions, and route corridor waypoints). Do not fabricate numbers.
2. Provide concise, clear, and highly actionable advice (typically 3-6 sentences).
3. For dangerous weather (storms, flash floods, dense fog), prioritize user and cargo safety.
5. If the user introduces, changes, or corrects their travel destination, origin, active location, cargo, or activity (e.g. 'What about Bangalore instead?', 'I meant Bangalore, not Hyderabad', 'Actually, let\'s go to Bangalore instead', 'What about Pune?'):
   - Explicitly confirm and use the updated location in your explanation.
   - You may optionally append a compact internal state tag at the very end of your response: <!--STATE: {"destination": "...", "origin": "...", "activity": "...", "cargo": "...", "date_time": "..."} -->.
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

_SUMMARIZATION_PROMPT = """\
You are a factual conversation summarizer for WeatherGPT.

Given the prior conversation summary and a set of older conversation turns, produce a NEW concise factual summary.

Capture ONLY:
- Locations mentioned (origin, destination, city, route)
- Travel or movement intent (e.g., transporting harvested rice from Hyderabad to Chennai)
- Cargo / crop type (e.g., harvested rice, grain, vegetables, cotton)
- Departure time or date constraints mentioned
- Weather concerns or decisions (e.g., user is worried about rain, planning early departure)
- Specific conditions or constraints (e.g., "user asked to avoid highways")
- Any explicit user decisions or plan changes

Do NOT include:
- Generic assistant boilerplate or greetings
- Repeated weather numbers that change each turn
- Filler phrases or pleasantries
- Fabricated context not present in the conversation

Be concise (2-5 sentences max). Return only the updated summary text.
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
    Detect if the message contains alert intent deterministically.
    Ensures exactly ONE LLM completion call in the normal chat flow.
    """
    return _rule_based_alert_intent(user_message)


async def generate_conversation_summary(
    existing_summary: str,
    messages_to_summarize: List[HistoryMessage],
) -> str:
    """
    Use Groq to produce a concise factual summary of older conversation turns.

    Args:
        existing_summary: The current/prior summary (may be empty for first summarization).
        messages_to_summarize: The older HistoryMessage turns outside the sliding window.

    Returns:
        Updated summary string.

    Raises:
        Exception on Groq failure (caller should catch and preserve existing summary).
    """
    client = _get_groq_client()

    # Build conversation transcript for summarization
    transcript_parts = []
    if existing_summary:
        transcript_parts.append(f"[Prior summary]\n{existing_summary}")
    transcript_parts.append("[Conversation turns to summarize]")
    for m in messages_to_summarize:
        label = "User" if m.role == "user" else "Assistant"
        transcript_parts.append(f"{label}: {m.content}")

    transcript = "\n\n".join(transcript_parts)

    async with cb_groq:
        completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": _SUMMARIZATION_PROMPT},
                {"role": "user", "content": transcript},
            ],
            model=settings.groq_model,
            temperature=0.1,
            max_tokens=300,
        )

    summary = (completion.choices[0].message.content or "").strip()
    logger.info("Conversation summary generated (%d chars)", len(summary))
    return summary


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
    route_waypoints: Optional[List[RouteWaypoint]] = None,
    travel_card: Optional[TravelCardData] = None,
    conversation_summary: Optional[str] = None,
    conversation_state: Optional[StructuredConversationState] = None,
) -> str:
    """
    Call Groq and return its answer as a plain string.

    Args:
        user_question: The user's current message
        weather_data: Primary location weather data
        history: Recent sliding-window conversation turns for context
        destination_weather: Destination weather for travel queries
        alert_suggestion: Structured alert suggestion if alert intent was detected
        forecast_summary: 7-day daily forecast summary for primary location
        destination_forecast_summary: 7-day daily forecast summary for destination
        tonight_summary: Tonight's weather/fog/rain summary for primary location
        destination_tonight_summary: Tonight's weather/fog/rain summary for destination
        route_waypoints: Intermediate pass-by places with weather along transit corridor
        travel_card: Structured Travel Weather Card data
        conversation_summary: Persistent conversation summary of older turns (hybrid memory)
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

    if route_waypoints:
        wp_data = [
            {
                "pass_by_stop": wp.name,
                "distance_from_origin_km": wp.distance_km,
                "temperature_c": wp.weather.temperature,
                "condition": wp.weather.condition,
                "rain_probability_pct": wp.weather.rain_probability,
                "humidity_pct": wp.weather.humidity,
                "wind_speed_ms": wp.weather.wind_speed,
            }
            for wp in route_waypoints
        ]
        weather_context += (
            f"\n\nIntermediate Route Corridor (pass-by waypoints along transit road):\n"
            f"```json\n{json.dumps(wp_data, indent=2)}\n```"
        )

    travel_card_note = ""
    if travel_card:
        travel_card_note = (
            f"\n\n[System Note: A dedicated Travel Weather Card is displayed to the user with the following validated data:\n"
            f"- Route: {travel_card.origin} → {travel_card.destination}\n"
            f"- Overall Risk Level: {travel_card.overall_risk} ({travel_card.risk_summary})\n"
            f"- Route Weather: {travel_card.route_weather_summary}\n"
            f"- Cargo: {travel_card.cargo or 'None'}\n"
            f"- Cargo Advice: {travel_card.cargo_risk_advice or 'N/A'}\n"
            f"- Timing: {travel_card.timing_note}\n"
            f"- Recommendation: {travel_card.recommendation}\n"
            f"Provide a natural-language response supporting this Travel Weather Card. Address the entire journey, cargo protection, and realistic conditions without inventing times or unsupported claims.]"
        )

    alert_note = ""
    if alert_suggestion:
        alert_note = (
            f"\n\n[System Note: An interactive alert card has been prepared for the user directly below your response "
            f"(Condition: {alert_suggestion.condition}, Threshold: {alert_suggestion.threshold}, Location: {alert_suggestion.location_name}). "
            f"Confirm you have prepared the alert card below for them to review and create with one click, and summarize current weather.]"
        )

    user_content = f"{weather_context}{alert_note}{travel_card_note}\n\nUser question: {user_question}"

    # Build hybrid memory context: structured state + rolling summary + sliding window
    memory_prefix = ""
    if conversation_state:
        state_parts = []
        if conversation_state.active_location:
            state_parts.append(f"Active location: {conversation_state.active_location}")
        if conversation_state.origin and conversation_state.destination:
            state_parts.append(f"Route: {conversation_state.origin} -> {conversation_state.destination}")
        elif conversation_state.destination:
            state_parts.append(f"Destination: {conversation_state.destination}")
        if conversation_state.activity:
            state_parts.append(f"Activity: {conversation_state.activity}")
        if conversation_state.cargo:
            state_parts.append(f"Cargo / Produce: {conversation_state.cargo}")
        if conversation_state.date_time:
            state_parts.append(f"Timing / Date: {conversation_state.date_time}")
        if conversation_state.current_weather_concern:
            state_parts.append(f"Weather concern: {conversation_state.current_weather_concern}")
        if conversation_state.previous_recommendation:
            state_parts.append(f"Previous advice: {conversation_state.previous_recommendation}")

        if state_parts:
            memory_prefix += (
                "[CURRENT CONVERSATION STATE — active memory for resolving 'there', 'it', 'the crop', etc.]\n"
                + "\n".join(f"- {line}" for line in state_parts)
                + "\n\n"
            )

    if conversation_summary and conversation_summary.strip():
        memory_prefix += (
            f"[CONVERSATION BACKGROUND — facts from earlier in this session, use for context resolution]\n"
            f"{conversation_summary.strip()}\n\n"
        )
    if memory_prefix:
        user_content = f"{memory_prefix}{user_content}"

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


def extract_and_validate_llm_state_updates(raw_answer: str) -> tuple[str, dict]:
    """
    Extract optional <!--STATE: {...} --> tag from LLM answer, validate its contents,
    and return (clean_answer, validated_updates_dict).
    Never blindly trust LLM-generated state.
    """
    if not raw_answer:
        return "", {}
    m = re.search(r"<!--\s*STATE:\s*(\{.*?\})\s*-->", raw_answer, re.DOTALL)
    if not m:
        return raw_answer, {}

    clean_answer = re.sub(r"<!--\s*STATE:\s*(\{.*?\})\s*-->", "", raw_answer, flags=re.DOTALL).strip()
    raw_json = m.group(1)
    validated = {}
    try:
        data = json.loads(raw_json)
        if isinstance(data, dict):
            allowed_fields = {
                "active_location", "origin", "destination", "activity",
                "cargo", "date_time", "current_weather_concern", "previous_recommendation"
            }
            for k, v in data.items():
                if k in allowed_fields and isinstance(v, str):
                    v_clean = v.strip()
                    if 0 < len(v_clean) < 100:
                        validated[k] = v_clean
    except Exception as exc:
        logger.debug("Failed parsing optional LLM state updates: %s", exc)

    return clean_answer, validated


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
