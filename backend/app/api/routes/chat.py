"""
app/api/routes/chat.py

POST /api/v1/chat
GET  /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
DELETE /api/v1/conversations/{conversation_id}

Request flow (chat endpoint):
  1. Validate request.
  2. Friendly greeting fast-path.
  3. Guardrail check — reject non-weather messages with 403.
  4. If authenticated: resolve or create conversation, retrieve hybrid context
     (persistent summary + recent sliding window).
  5. Contextual Reference Resolution:
     - If follow-up detected ("tomorrow", "there", location switch, cargo reference):
       resolve from recent window → then summary.
  6. Detect travel intent → resolve BOTH origin + destination.
  7. Resolve primary location (explicit → extracted/resolved from context → GPS).
  8. Detect tomorrow question.
  9. Fetch weather (origin + optional destination) from Open-Meteo.
 10. Fetch route waypoints & travel weather card.
 11. Run alert intent detection + generate LLM response with hybrid context.
 12. Persist user + assistant messages; trigger rolling summarization if threshold exceeded.
 13. Return structured ChatResponse with conversation_id.
"""

import asyncio
import logging
import re
import time
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.resilience import ServiceUnavailableError
from app.schemas.chat import (
    AlertSuggestion,
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationSummary,
    HistoryMessage,
    RouteWaypoint,
    TravelCardData,
    WeatherData,
)
from app.services.llm_service import build_alert_suggestion, generate_weather_response
from app.services.location_service import (
    compute_travel_risk,
    detect_cargo,
    detect_departure_timing,
    extract_location_from_message,
    extract_locations_from_message,
    extract_route_info,
    extract_travel_destination,
    geocode_location,
    get_route_waypoints,
    resolve_location,
)
from app.services.weather_service import get_weather
from app.utils.weather_guardrail import is_weather_related

settings = get_settings()

logger = logging.getLogger(__name__)

router = APIRouter()

_TOMORROW_KEYWORDS = ["tomorrow", "next day", "day after today"]
_FORECAST_KEYWORDS = [
    "week", "weekly", "forecast", "days", "next few days", "coming days",
    "next week", "agriculture", "agricultural", "farming", "farm", "crops",
    "crop", "paddy", "harvest", "pest", "fungus", "irrigate", "irrigation",
    "spoilage", "outlook",
]
_TONIGHT_KEYWORDS = ["tonight", "night", "overnight", "this evening", "late night", "fog"]

# Reference indicators — follow-up patterns that signal context should be resolved
# IMPORTANT: Only short, ambiguous messages that CANNOT stand alone as weather queries.
# Do NOT include patterns like 'the weather', 'is it', 'will it' — those appear in
# first-turn queries too (e.g. 'What is the weather in X?', 'Will it rain in Delhi?')
_FOLLOW_UP_PATTERNS = [
    # Temporal shorthand with NO location
    r"^\s*what about tomorrow\s*\??\s*$",
    r"^\s*how about tomorrow\s*\??\s*$",
    r"^\s*and tomorrow\s*\??\s*$",
    r"^\s*next day\??\s*$",
    # Pure "what about X" / "how about X" — short follow-ups
    r"^\s*what about\b",
    r"^\s*how about\b",
    r"^\s*and\s+(in\s+)?[a-z]{2,20}\??\s*$",
    # "there" / "same place" pronoun references
    r"\bthere\b",
    r"\bsame place\b",
    r"\bsame route\b",
    r"\bsame location\b",
    # Cargo/crop pronoun references
    r"\bthe crop\b",
    r"\bthe goods\b",
    r"\bthe cargo\b",
    r"\bthe rice\b",
]


def _is_tomorrow_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TOMORROW_KEYWORDS)


def _is_forecast_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _FORECAST_KEYWORDS)


def _is_tonight_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TONIGHT_KEYWORDS)


def _is_follow_up(message: str) -> bool:
    """Detect if message is a follow-up that needs conversational context."""
    lower = message.lower()
    return any(re.search(p, lower) for p in _FOLLOW_UP_PATTERNS)


def _extract_location_override_from_followup(message: str) -> Optional[str]:
    """
    Extract explicit location from a follow-up message like:
    'What about Chennai?' / 'How about Pune?'
    Returns the location name, or None if not found.
    """
    patterns = [
        r"\bwhat about\s+([A-Za-z\s,-]{2,30})\??$",
        r"\bhow about\s+([A-Za-z\s,-]{2,30})\??$",
        r"\band\s+([A-Za-z\s,-]{2,30})\??$",
    ]
    for pat in patterns:
        m = re.search(pat, message.strip(), re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().rstrip("?.,! ")
            if len(candidate) >= 2 and candidate.lower() not in {
                "tomorrow", "today", "tonight", "now", "there", "it",
                "the weather", "the crop", "next day"
            }:
                return candidate
    return None


def _extract_context_location(
    recent_messages: List[HistoryMessage],
    summary: str,
) -> Optional[str]:
    """
    Walk recent messages in reverse chronological order, then the summary,
    to find the most recently mentioned location.
    User messages are searched first (most recent to oldest) to capture direct user intent
    (including location overrides like "What about Chennai?"), followed by assistant messages,
    then the conversation summary.
    """
    # 1. User messages (most recent first)
    for msg in reversed(recent_messages):
        if msg.role == "user":
            override = _extract_location_override_from_followup(msg.content)
            if override:
                return override
            loc = extract_location_from_message(msg.content)
            if loc:
                return loc

    # 2. Assistant messages (in case location was geocoded or detected from GPS)
    for msg in reversed(recent_messages):
        if msg.role == "assistant":
            loc = extract_location_from_message(msg.content)
            if loc:
                return loc

    # 3. Fall back to summary
    if summary:
        loc = extract_location_from_message(summary)
        if loc:
            return loc
    return None


def _extract_context_destination(
    recent_messages: List[HistoryMessage],
    summary: str,
) -> Optional[str]:
    """
    Walk recent messages in reverse chronological order, then summary,
    to find the most recently mentioned travel destination.
    """
    for msg in reversed(recent_messages):
        if msg.role == "user":
            _, dest = extract_route_info(msg.content)
            if dest:
                return dest
            dest = extract_travel_destination(msg.content)
            if dest:
                return dest
    for msg in reversed(recent_messages):
        if msg.role == "assistant":
            _, dest = extract_route_info(msg.content)
            if dest:
                return dest
            dest = extract_travel_destination(msg.content)
            if dest:
                return dest
    if summary:
        _, dest = extract_route_info(summary)
        if dest:
            return dest
        dest = extract_travel_destination(summary)
        if dest:
            return dest
    return None


def _build_fallback_answer(
    weather_data: WeatherData,
    destination: Optional[WeatherData] = None,
) -> str:
    """Produce a structured plain-text answer when the LLM is unavailable."""
    lines = [
        f"Current weather in {weather_data.location}:",
        "",
        f"  Temperature   : {weather_data.temperature} °C (feels like {weather_data.feels_like} °C)",
        f"  Condition     : {weather_data.condition}",
        f"  Humidity      : {weather_data.humidity} %",
        f"  Wind speed    : {weather_data.wind_speed} m/s",
    ]
    if weather_data.rain_probability is not None:
        lines.append(f"  Rain chance   : {weather_data.rain_probability} %")
    if weather_data.rainfall is not None:
        lines.append(f"  Rainfall      : {weather_data.rainfall} mm")

    if destination:
        lines += [
            "",
            f"Weather at destination ({destination.location}):",
            "",
            f"  Temperature   : {destination.temperature} °C (feels like {destination.feels_like} °C)",
            f"  Condition     : {destination.condition}",
            f"  Humidity      : {destination.humidity} %",
            f"  Wind speed    : {destination.wind_speed} m/s",
        ]
        if destination.rain_probability is not None:
            lines.append(f"  Rain chance   : {destination.rain_probability} %")

    lines.append("")
    lines.append("The conversational AI is temporarily unavailable.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main chat endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a weather question",
    description=(
        "Send a natural-language weather question (max 2000 characters). "
        "Supports conversation history, travel queries, alert intent detection, "
        "and conversational memory (sliding window + persistent summary). "
        "Non-weather questions are rejected with 403."
    ),
    responses={
        200: {"description": "Successful weather answer."},
        400: {"description": "Empty or invalid message."},
        403: {"description": "Question is not weather-related."},
        404: {"description": "Location not found."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream API error."},
        503: {"description": "Upstream API temporarily unavailable."},
    },
)
@limiter.limit(settings.rate_limit_chat)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Handle a single user weather question end-to-end."""
    start = time.perf_counter()
    logger.info(
        "Chat request | message='%s' | location='%s' | coords=(%s, %s) | conv_id='%s'",
        body.message[:80], body.location, body.latitude, body.longitude,
        body.conversation_id,
    )

    # ── 1. Friendly greetings fast-path ──────────────────────────────────────
    clean_msg = body.message.strip().lower().rstrip("?.! ")
    _GREETINGS = {
        "hi", "hello", "hey", "help", "who are you", "what can you do",
        "good morning", "good afternoon", "good evening", "greetings"
    }
    if clean_msg in _GREETINGS:
        return ChatResponse(
            answer=(
                "Hello! I'm WeatherGPT, your conversational weather AI. "
                "Ask me about current conditions, forecasts, or travel weather "
                "for any city worldwide — or say 'alert me when it rains in Delhi' "
                "to set a weather alert!"
            ),
            location="Global",
            weather_data=None,
        )

    # ── 2. Guardrail ─────────────────────────────────────────────────────────
    # Follow-up messages in an active conversation are weather-related by context
    # even if they don't contain explicit weather keywords (e.g. "What about tomorrow?")
    # We skip the guardrail for short follow-ups when the user has conversation history.
    has_history = bool(body.history) or bool(body.conversation_id)
    is_potential_followup = _is_follow_up(body.message)
    skip_guardrail = has_history and is_potential_followup and len(body.message.strip()) < 80

    if not skip_guardrail and not is_weather_related(body.message):
        logger.info("Request rejected by guardrail | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    # ── 3. Resolve authenticated user & conversation context ─────────────────
    from app.core.auth import get_optional_current_user
    auth_user = get_optional_current_user(request)

    conversation_id: Optional[str] = None
    conversation_summary: str = ""
    # Start with history from request body (guest / client-side sliding window)
    history_messages: List[HistoryMessage] = [
        HistoryMessage(role=m.role, content=m.content)
        for m in (body.history or [])
    ]

    if auth_user:
        from app.services.conversation_service import (
            get_or_create_conversation,
            get_conversation_context,
        )
        conv = await get_or_create_conversation(
            conversation_id=body.conversation_id,
            user_id=auth_user.id,
        )
        conversation_id = conv.id
        # Load server-side hybrid context (overrides client-sent history)
        conversation_summary, history_messages = await get_conversation_context(
            conversation_id=conversation_id,
            user_id=auth_user.id,
        )
        logger.info(
            "Conversation context loaded | conv_id='%s' | summary_len=%d | recent_msgs=%d",
            conversation_id, len(conversation_summary), len(history_messages)
        )

    # ── 4. Contextual Reference Resolution ───────────────────────────────────
    #
    # A follow-up is a message that:
    #   - Has conversation history (prior turns available)
    #   - Contains a pronoun reference OR starts with "what about" / "how about"
    #   - Does NOT contain an explicit location name by itself (it relies on context)
    #
    is_followup = _is_follow_up(body.message) and (has_history or bool(history_messages) or bool(conversation_summary))
    context_location: Optional[str] = None
    context_destination: Optional[str] = None

    if is_followup and (history_messages or conversation_summary):
        # Check for explicit location switch in follow-up: "What about Chennai?"
        location_override = _extract_location_override_from_followup(body.message)
        if location_override:
            # User is switching location/destination
            context_location = location_override
            logger.info("Follow-up location override detected: '%s'", location_override)
        else:
            # Resolve from recent window (priority) → summary
            context_location = _extract_context_location(history_messages, conversation_summary)
            context_destination = _extract_context_destination(history_messages, conversation_summary)
            logger.info(
                "Follow-up resolved from context: location='%s', destination='%s'",
                context_location, context_destination
            )

    # ── 5. Travel intent detection ────────────────────────────────────────────
    route_orig, route_dest = extract_route_info(body.message)
    travel_dest_name = route_dest or extract_travel_destination(body.message)
    msg_locs = extract_locations_from_message(body.message)

    # If no explicit travel destination in message but one was resolved from context
    if not travel_dest_name and context_destination:
        travel_dest_name = context_destination

    # ── 6. Resolve primary location ──────────────────────────────────────────
    try:
        # For a genuine follow-up with no new location, suppress message extraction
        # and use context_location instead — prevents GPS fallback.
        # For normal queries (not follow-up), always let message extraction win.
        explicit_loc = body.location
        resolve_message = body.message  # default: use full message for extraction

        if is_followup:
            if context_location:
                # Follow-up with resolved context: use that as explicit, skip message extraction
                explicit_loc = context_location
                resolve_message = None
            else:
                # Follow-up but no context found: still use message (may contain new location)
                pass

        resolved = await resolve_location(
            explicit=explicit_loc,
            message=resolve_message,
            latitude=body.latitude,
            longitude=body.longitude,
        )

        # If resolution from message failed but we have a context location, try that
        if not resolved and context_location:
            try:
                lat, lon, dname = await geocode_location(context_location)
                from app.services.location_service import ResolvedLocation
                resolved = ResolvedLocation(
                    name=dname, latitude=lat, longitude=lon, source="context"
                )
            except Exception:
                pass

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Location service is temporarily unavailable.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Location resolution error.") from exc

    if not resolved:
        return ChatResponse(
            answer=(
                "I need your location to check the weather. Please mention a city name "
                "(e.g. 'What's the weather in Chennai?') or allow browser location access."
            ),
            location="Unknown",
            conversation_id=conversation_id,
        )

    logger.info("Primary location resolved: '%s' (source=%s)", resolved.name, resolved.source)

    # Save to user recent locations if authenticated
    if auth_user and resolved and resolved.name:
        from app.services.recent_locations_service import add_recent_location
        await add_recent_location(
            user_id=auth_user.id,
            location_name=resolved.name,
            latitude=resolved.latitude,
            longitude=resolved.longitude,
        )

    # ── 7. Resolve secondary/destination location (if any) ───────────────────
    secondary_query = None
    if route_dest and route_dest.lower() not in resolved.name.lower() and resolved.name.lower() not in route_dest.lower():
        secondary_query = route_dest
    elif travel_dest_name and travel_dest_name.lower() not in resolved.name.lower() and resolved.name.lower() not in travel_dest_name.lower():
        secondary_query = travel_dest_name
    elif len(msg_locs) >= 2 and msg_locs[1].lower() not in resolved.name.lower():
        secondary_query = msg_locs[1]
    elif len(msg_locs) == 1 and body.location and body.location.strip().lower() not in resolved.name.lower():
        secondary_query = body.location.strip()

    destination_resolved = None
    if secondary_query:
        try:
            dest_lat, dest_lon, dest_name = await geocode_location(secondary_query)
            from app.services.location_service import ResolvedLocation
            destination_resolved = ResolvedLocation(
                name=dest_name,
                latitude=dest_lat,
                longitude=dest_lon,
                source="extracted",
            )
            logger.info("Secondary location resolved: '%s'", dest_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resolve secondary location '%s': %s", secondary_query, exc)

    # ── 8. Fetch weather data ─────────────────────────────────────────────────
    for_tomorrow = _is_tomorrow_question(body.message)
    try:
        weather_data = await get_weather(
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            location_name=resolved.name,
            for_tomorrow=for_tomorrow,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather service is temporarily unavailable.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Weather service error.") from exc

    destination_weather: Optional[WeatherData] = None
    if destination_resolved:
        try:
            destination_weather = await get_weather(
                latitude=destination_resolved.latitude,
                longitude=destination_resolved.longitude,
                location_name=destination_resolved.name,
                for_tomorrow=for_tomorrow,
            )
            logger.info("Secondary weather fetched for '%s'", destination_resolved.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch secondary weather: %s", exc)

    # ── 8b. Fetch route waypoints & corridor weather ──────────────────────────
    route_waypoints: Optional[List[RouteWaypoint]] = None
    if (
        destination_resolved
        and resolved.name.lower() not in destination_resolved.name.lower()
        and destination_resolved.name.lower() not in resolved.name.lower()
    ):
        try:
            raw_wps = await get_route_waypoints(
                orig_lat=resolved.latitude,
                orig_lon=resolved.longitude,
                dest_lat=destination_resolved.latitude,
                dest_lon=destination_resolved.longitude,
            )
            if raw_wps:
                wp_weather_tasks = [
                    get_weather(
                        latitude=wp["latitude"],
                        longitude=wp["longitude"],
                        location_name=wp["name"],
                        for_tomorrow=for_tomorrow,
                    )
                    for wp in raw_wps
                ]
                wp_weathers = await asyncio.gather(*wp_weather_tasks, return_exceptions=True)
                route_waypoints = []
                for wp, w_res in zip(raw_wps, wp_weathers):
                    if isinstance(w_res, WeatherData):
                        route_waypoints.append(
                            RouteWaypoint(
                                name=wp["name"],
                                latitude=wp["latitude"],
                                longitude=wp["longitude"],
                                distance_km=wp.get("distance_km"),
                                weather=w_res,
                            )
                        )
                if not route_waypoints:
                    route_waypoints = None
                else:
                    logger.info("Resolved %d intermediate route waypoints with weather", len(route_waypoints))
        except Exception as wp_err:  # noqa: BLE001
            logger.warning("Failed fetching route waypoints: %s", wp_err)

    # ── 8c. Detect cargo, timing & compute Travel Weather Card ────────────────
    travel_card: Optional[TravelCardData] = None
    cargo = detect_cargo(body.message)
    departure_timing = detect_departure_timing(body.message)

    if destination_resolved and destination_weather:
        try:
            travel_card = compute_travel_risk(
                origin_name=resolved.name,
                origin_weather=weather_data,
                destination_name=destination_resolved.name,
                destination_weather=destination_weather,
                waypoints=route_waypoints or [],
                cargo=cargo,
                departure_timing=departure_timing,
            )
            logger.info(
                "Travel Weather Card created | %s -> %s | Risk=%s",
                travel_card.origin, travel_card.destination, travel_card.overall_risk,
            )
        except Exception as tc_err:  # noqa: BLE001
            logger.warning("Failed to compute TravelCardData: %s", tc_err)

    # ── 9. Fetch 7-day forecast and tonight conditions ────────────────────────
    from app.services.weather_service import get_forecast

    is_forecast = _is_forecast_question(body.message)
    is_tonight = _is_tonight_question(body.message) or (route_dest is not None)

    forecast_summary = None
    destination_forecast_summary = None
    tonight_summary = None
    destination_tonight_summary = None

    if is_forecast:
        try:
            f_resp = await get_forecast(resolved.latitude, resolved.longitude, resolved.name, days=7)
            forecast_summary = [
                {
                    "date": d.date,
                    "condition": d.condition,
                    "max_temp_c": d.temperature_max,
                    "min_temp_c": d.temperature_min,
                    "rain_chance_pct": d.rain_probability,
                    "precipitation_mm": d.precipitation,
                    "wind_speed_ms": d.wind_speed,
                }
                for d in f_resp.forecast
            ]
        except Exception as f_err:
            logger.warning("Could not fetch 7-day forecast for %s: %s", resolved.name, f_err)

        if destination_resolved:
            try:
                dest_f_resp = await get_forecast(
                    destination_resolved.latitude, destination_resolved.longitude, destination_resolved.name, days=7
                )
                destination_forecast_summary = [
                    {
                        "date": d.date,
                        "condition": d.condition,
                        "max_temp_c": d.temperature_max,
                        "min_temp_c": d.temperature_min,
                        "rain_chance_pct": d.rain_probability,
                        "precipitation_mm": d.precipitation,
                        "wind_speed_ms": d.wind_speed,
                    }
                    for d in dest_f_resp.forecast
                ]
            except Exception as f_err:
                logger.warning("Could not fetch destination forecast for %s: %s", destination_resolved.name, f_err)

    if is_tonight:
        def _fog_assessment(hum: int, wind: float) -> str:
            if hum >= 85 and wind <= 2.5:
                return "HIGH (Dense fog or low mist likely overnight; reduced visibility)"
            elif hum >= 75 and wind <= 3.5:
                return "MODERATE (Patchy mist or morning fog possible)"
            return "LOW (Good visibility expected)"

        tonight_summary = {
            "temperature_c": weather_data.temperature,
            "humidity_pct": weather_data.humidity,
            "wind_speed_ms": weather_data.wind_speed,
            "condition": weather_data.condition,
            "rain_probability_pct": weather_data.rain_probability,
            "fog_risk": _fog_assessment(weather_data.humidity, weather_data.wind_speed),
        }

        if destination_weather:
            destination_tonight_summary = {
                "temperature_c": destination_weather.temperature,
                "humidity_pct": destination_weather.humidity,
                "wind_speed_ms": destination_weather.wind_speed,
                "condition": destination_weather.condition,
                "rain_probability_pct": destination_weather.rain_probability,
                "fog_risk": _fog_assessment(destination_weather.humidity, destination_weather.wind_speed),
            }

    # ── 10. Build alert suggestion & generate LLM response ────────────────────
    alert_suggestion = None
    created_alert = None
    answer = ""
    try:
        raw_alert = await build_alert_suggestion(
            body.message,
            weather_data,
            latitude=resolved.latitude,
            longitude=resolved.longitude,
        )
        if raw_alert and destination_weather and destination_resolved:
            alert_suggestion = AlertSuggestion(
                location_name=destination_resolved.name,
                latitude=destination_resolved.latitude,
                longitude=destination_resolved.longitude,
                condition=raw_alert.condition,
                threshold=raw_alert.threshold,
                description=raw_alert.description.replace(
                    weather_data.location, destination_resolved.name
                ),
            )
        elif raw_alert:
            alert_suggestion = raw_alert

        # Auto-create alert if authenticated
        if alert_suggestion:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]
                try:
                    from app.core.auth import _decode_jwt
                    payload = _decode_jwt(token)
                    user_id = payload.get("sub")
                    if user_id:
                        from app.schemas.alert import AlertRequest
                        from app.services.alert_service import create_alert
                        alert_req = AlertRequest(
                            latitude=alert_suggestion.latitude,
                            longitude=alert_suggestion.longitude,
                            condition=alert_suggestion.condition,
                            threshold=alert_suggestion.threshold,
                            location_name=alert_suggestion.location_name,
                        )
                        alert_obj = await create_alert(alert_req, user_id=user_id)
                        created_alert = alert_obj.model_dump()
                        logger.info("Auto-created alert %s for user %s from chat query", alert_obj.id, user_id)
                except Exception as auto_err:
                    logger.warning("Could not auto-create alert from chat: %s", auto_err)

        # Build history list from HistoryMessage objects (for LLM)
        llm_history = history_messages if history_messages else None

        answer = await generate_weather_response(
            user_question=body.message,
            weather_data=weather_data,
            history=llm_history,
            destination_weather=destination_weather,
            alert_suggestion=alert_suggestion,
            forecast_summary=forecast_summary,
            destination_forecast_summary=destination_forecast_summary,
            tonight_summary=tonight_summary,
            destination_tonight_summary=destination_tonight_summary,
            route_waypoints=route_waypoints,
            travel_card=travel_card,
            conversation_summary=conversation_summary if conversation_summary else None,
        )
    except httpx.TimeoutException:
        logger.warning("LLM timed out; returning structured fallback for '%s'", resolved.name)
        answer = _build_fallback_answer(weather_data, destination_weather)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM/alert error: %s", exc)
        answer = _build_fallback_answer(weather_data, destination_weather)

    # ── 11. Persist messages & trigger summarization ──────────────────────────
    if auth_user and conversation_id and answer:
        from app.services.conversation_service import (
            add_message,
            check_and_trigger_summarization,
        )
        try:
            # Persist user message + assistant answer
            await add_message(conversation_id, auth_user.id, "user", body.message)
            await add_message(conversation_id, auth_user.id, "assistant", answer)

            # Rolling summarization check (fail-safe — errors do not crash chat)
            new_summary = await check_and_trigger_summarization(
                conversation_id=conversation_id,
                user_id=auth_user.id,
                existing_summary=conversation_summary,
                recent_messages=history_messages,
            )
            if new_summary:
                logger.info("Conversation %s summary updated (%d chars)", conversation_id, len(new_summary))
        except Exception as persist_err:  # noqa: BLE001
            logger.error("Failed to persist conversation messages: %s", persist_err)

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed in %.1f ms | location='%s' | travel=%s | waypoints=%d | conv_id='%s'",
        elapsed,
        resolved.name,
        destination_resolved is not None,
        len(route_waypoints) if route_waypoints else 0,
        conversation_id,
    )

    return ChatResponse(
        answer=answer,
        location=resolved.name,
        weather_data=weather_data,
        destination_weather=destination_weather,
        route_waypoints=route_waypoints,
        travel_card=travel_card,
        alert_suggestion=alert_suggestion,
        created_alert=created_alert,
        conversation_id=conversation_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Conversation management endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/conversations",
    summary="List user's conversations",
    description="Returns all conversations for the authenticated user, ordered by most recently updated.",
)
async def list_conversations_endpoint(request: Request):
    """List all conversations for the authenticated user."""
    from app.core.auth import get_current_user
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi import Depends

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    token = auth_header.split(" ", 1)[1]
    try:
        from app.core.auth import _decode_jwt, AuthUser
        payload = _decode_jwt(token)
        user_id = payload.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.") from exc

    from app.services.conversation_service import list_conversations
    conversations = await list_conversations(user_id)
    return {"conversations": [c.model_dump() for c in conversations]}


@router.get(
    "/conversations/{conversation_id}",
    summary="Get conversation details",
    description="Returns the conversation summary and recent messages for the given conversation.",
)
async def get_conversation_endpoint(conversation_id: str, request: Request):
    """Get a specific conversation (with recent messages) for the authenticated user."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    token = auth_header.split(" ", 1)[1]
    try:
        from app.core.auth import _decode_jwt
        payload = _decode_jwt(token)
        user_id = payload.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.") from exc

    from app.services.conversation_service import _fetch_conversation, get_conversation_context
    conv = await _fetch_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    summary, recent = await get_conversation_context(conversation_id, user_id)
    return ConversationDetail(
        conversation=conv,
        recent_messages=recent,
    ).model_dump()


@router.delete(
    "/conversations/{conversation_id}",
    summary="Delete a conversation",
    description="Deletes a conversation and all its messages for the authenticated user.",
)
async def delete_conversation_endpoint(conversation_id: str, request: Request):
    """Delete a conversation for the authenticated user."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    token = auth_header.split(" ", 1)[1]
    try:
        from app.core.auth import _decode_jwt
        payload = _decode_jwt(token)
        user_id = payload.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.") from exc

    from app.services.conversation_service import delete_conversation
    deleted = await delete_conversation(conversation_id, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"deleted": True, "conversation_id": conversation_id}
