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
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

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
    StructuredConversationState,
    TravelCardData,
    WeatherData,
)
from app.services.llm_service import (
    build_alert_suggestion,
    extract_and_validate_llm_state_updates,
    generate_weather_response,
)
from app.services.conversation_service import (
    add_message,
    async_summarize_if_needed,
    get_conversation_context_and_state,
    get_conversation_state,
    get_or_create_conversation,
    merge_conversation_state,
    save_conversation_state,
)
from app.services.location_service import (
    ResolvedLocation,
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
    resolve_state_aware_locations,
)
from app.services.weather_service import get_weather
from app.utils.weather_guardrail import _contains_injection_attempt, is_weather_related

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


def _is_tomorrow_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TOMORROW_KEYWORDS)


def _is_forecast_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _FORECAST_KEYWORDS)


def _is_tonight_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TONIGHT_KEYWORDS)


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
        lines.append("")
        lines.append(f"Destination weather in {destination.location}:")
        lines.append(f"  Temperature   : {destination.temperature} °C (feels like {destination.feels_like} °C)")
        lines.append(f"  Condition     : {destination.condition}")
        lines.append(f"  Humidity      : {destination.humidity} %")
        lines.append(f"  Wind speed    : {destination.wind_speed} m/s")
        if destination.rain_probability is not None:
            lines.append(f"  Rain chance   : {destination.rain_probability} %")

    lines.append("")
    lines.append("The conversational AI is temporarily unavailable.")
    return "\n".join(lines)


async def _persist_turn_and_summarize(
    conv_id: str,
    user_id: str,
    user_msg: str,
    assistant_msg: str,
    existing_summary: str,
    recent_msgs: List[HistoryMessage],
) -> None:
    """Asynchronous background worker: persists message turns and checks rolling summarization without blocking the response."""
    try:
        await add_message(conv_id, user_id, "user", user_msg)
        await add_message(conv_id, user_id, "assistant", assistant_msg)
        await async_summarize_if_needed(conv_id, user_id, existing_summary, recent_msgs)
    except Exception as exc:
        logger.error("Background turn persistence / summarization error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main chat endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a weather question",
    description=(
        "Send a natural-language weather question (max 2000 characters). "
        "Supports conversation state, hybrid context (sliding window + persistent summary), "
        "travel queries, alert intent detection, and fast Redis caching."
    ),
    responses={
        200: {"description": "Successful weather answer."},
        400: {"description": "Empty or invalid message."},
        403: {"description": "Security policy or prompt-injection violation."},
        404: {"description": "Location not found."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream API error."},
        503: {"description": "Upstream API temporarily unavailable."},
    },
)
@limiter.limit(settings.rate_limit_chat)
async def chat(
    request: Request,
    body: ChatRequest,
    background_tasks: BackgroundTasks,
) -> ChatResponse:
    """Handle a single user weather question end-to-end with structured memory."""
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

    # ── 2. Security Guardrail (Prompt-injection & severe abuse rejection) ────
    if _contains_injection_attempt(body.message):
        logger.info("Request rejected by security guardrail | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    # ── 3. Resolve user identity and retrieve context + structured state ─────
    from app.core.auth import get_optional_current_user
    auth_user = get_optional_current_user(request)
    user_id = auth_user.id if auth_user else "guest"

    conversation_id: Optional[str] = body.conversation_id
    conversation_summary: str = ""
    history_messages: List[HistoryMessage] = [
        HistoryMessage(role=m.role, content=m.content)
        for m in (body.history or [])
    ]
    conversation_state: StructuredConversationState

    if auth_user:
        conv = await get_or_create_conversation(
            conversation_id=body.conversation_id,
            user_id=auth_user.id,
        )
        conversation_id = conv.id
        conversation_summary, history_messages, conversation_state = await get_conversation_context_and_state(
            conversation_id=conversation_id,
            user_id=auth_user.id,
        )
        logger.info(
            "Loaded context for conv=%s | user=%s | summary_len=%d | recent_msgs=%d | state_loc=%s",
            conversation_id, user_id, len(conversation_summary), len(history_messages), conversation_state.active_location
        )
    else:
        # Guest context: check Redis state if conversation_id provided
        if conversation_id:
            cached_guest_state = await get_conversation_state(conversation_id, "guest")
            conversation_state = cached_guest_state or StructuredConversationState(user_id="guest")
        else:
            from app.services.conversation_service import _new_conv_id
            conversation_id = _new_conv_id()
            conversation_state = StructuredConversationState(user_id="guest")

    # ── 4. Relevance & Conversational Continuity Check ───────────────────────
    # In an ongoing conversation (active state or existing history), the context is
    # already established — follow-ups (e.g. "what about tomorrow?", "when should I go?",
    # "will it affect the crop?") must NEVER be rejected for lacking weather keywords.
    has_active_context = (
        bool(conversation_state.active_location)
        or bool(conversation_state.origin)
        or bool(history_messages)
        or bool(conversation_summary)
        or bool(body.history)
    )
    if not has_active_context and not is_weather_related(body.message):
        logger.info("Cold query rejected: not weather related | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    # ── 5. State-Aware Location Resolution & Updates ─────────────────────────
    # Uses current message + structured conversation state to determine active locations
    # without relying on regex as the sole authority for active location determination.
    has_prior_location = bool(
        conversation_state.active_location
        or conversation_state.origin
        or conversation_state.activity == "travel"
    )

    loc_res = resolve_state_aware_locations(
        message=body.message,
        state=conversation_state,
        explicit_location=body.location,
    )

    cargo = detect_cargo(body.message)
    departure_timing = detect_departure_timing(body.message)
    for_tomorrow = _is_tomorrow_question(body.message)
    is_tonight = _is_tonight_question(body.message)

    state_updates = dict(loc_res.state_updates)
    if cargo:
        state_updates["cargo"] = cargo
    if departure_timing:
        state_updates["date_time"] = departure_timing
    elif for_tomorrow:
        state_updates["date_time"] = "tomorrow"
    elif is_tonight:
        state_updates["date_time"] = "tonight"

    conversation_state = merge_conversation_state(conversation_state, state_updates)

    # If state does not have an active location yet, inspect recent conversation history to hydrate
    if not conversation_state.active_location and not conversation_state.origin and history_messages:
        for hm in reversed(history_messages):
            orig, dest = extract_route_info(hm.content)
            if orig:
                conversation_state.origin = orig
                conversation_state.active_location = orig
            if dest:
                conversation_state.destination = dest
            if not conversation_state.active_location:
                loc = extract_location_from_message(hm.content)
                if loc:
                    conversation_state.active_location = loc
            if conversation_state.active_location:
                break

    # ── 6. Resolve Primary & Secondary Locations for Weather Data ────────────
    # Step 6.1: Resolve Primary Location
    resolved: Optional[ResolvedLocation] = None

    # In travel mode or follow-ups with an established prior context, resolve state primary_query
    if loc_res.primary_query and has_prior_location:
        try:
            resolved = await resolve_location(
                explicit=loc_res.primary_query,
                message=None,
                latitude=body.latitude if (loc_res.primary_query == body.location) else None,
                longitude=body.longitude if (loc_res.primary_query == body.location) else None,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )
        except Exception as exc:
            logger.warning("Could not resolve state primary location '%s': %s", loc_res.primary_query, exc)

    # Standalone initial query or direct query resolution
    if not resolved:
        try:
            resolved = await resolve_location(
                explicit=body.location,
                message=body.message,
                latitude=body.latitude,
                longitude=body.longitude,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )
        except Exception as exc:
            logger.warning("Fallback location resolution failed: %s", exc)

    # State location fallback if no location in message
    if not resolved and (loc_res.primary_query or conversation_state.active_location or conversation_state.origin):
        state_loc = loc_res.primary_query or conversation_state.active_location or conversation_state.origin
        try:
            lat, lon, dname = await geocode_location(state_loc)
            resolved = ResolvedLocation(name=dname, latitude=lat, longitude=lon, source="state")
        except Exception as exc:
            logger.warning("Could not geocode state location '%s': %s", state_loc, exc)

    # GPS fallback if no location was resolved yet
    if not resolved and (body.latitude is not None and body.longitude is not None):
        try:
            resolved = await resolve_location(
                explicit=None,
                message=None,
                latitude=body.latitude,
                longitude=body.longitude,
            )
        except Exception as exc:
            logger.warning("Fallback GPS resolution failed: %s", exc)

    if not resolved:
        return ChatResponse(
            answer=(
                "I need your location to check the weather. Please mention a city name "
                "(e.g. 'What's the weather in Chennai?') or allow browser location access."
            ),
            location="Unknown",
            conversation_id=conversation_id,
            conversation_state=conversation_state,
        )

    # Update conversation state with resolved primary location
    if not loc_res.is_travel:
        conversation_state.active_location = resolved.name
    elif not conversation_state.origin:
        conversation_state.origin = resolved.name
        conversation_state.active_location = resolved.name

    # Step 6.2: Resolve Destination if travel scenario
    destination_resolved: Optional[ResolvedLocation] = None
    destination_query = (
        loc_res.destination_query
        or (conversation_state.destination if conversation_state.activity == "travel" else None)
    )
    if destination_query and resolved.name.lower() not in destination_query.lower():
        try:
            d_lat, d_lon, d_name = await geocode_location(destination_query)
            destination_resolved = ResolvedLocation(name=d_name, latitude=d_lat, longitude=d_lon, source="destination")
            conversation_state.destination = d_name
            conversation_state.activity = "travel"
            logger.info("Destination location resolved: '%s'", d_name)
        except Exception as exc:
            logger.warning("Could not resolve destination '%s': %s", destination_query, exc)

    # Save to user recent locations if authenticated
    if auth_user and resolved and resolved.name:
        from app.services.recent_locations_service import add_recent_location
        await add_recent_location(
            user_id=auth_user.id,
            location_name=resolved.name,
            latitude=resolved.latitude,
            longitude=resolved.longitude,
        )

    # ── 7. Concurrent Weather Retrieval via Redis Cache ──────────────────────
    # Concurrently fetch primary and secondary weather data using asyncio.gather
    weather_tasks = [
        get_weather(
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            location_name=resolved.name,
            for_tomorrow=for_tomorrow,
        )
    ]
    if destination_resolved:
        weather_tasks.append(
            get_weather(
                latitude=destination_resolved.latitude,
                longitude=destination_resolved.longitude,
                location_name=destination_resolved.name,
                for_tomorrow=for_tomorrow,
            )
        )

    weather_results = await asyncio.gather(*weather_tasks, return_exceptions=True)
    if isinstance(weather_results[0], httpx.TimeoutException):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather service is temporarily unavailable. Please try again in a moment.",
        ) from weather_results[0]
    elif isinstance(weather_results[0], (httpx.HTTPStatusError, ServiceUnavailableError)):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error communicating with upstream weather service.",
        ) from weather_results[0]
    elif isinstance(weather_results[0], Exception):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Weather service error: {weather_results[0]}",
        ) from weather_results[0]

    weather_data: WeatherData = weather_results[0]

    destination_weather: Optional[WeatherData] = None
    if len(weather_results) > 1 and isinstance(weather_results[1], WeatherData):
        destination_weather = weather_results[1]

    # ── 8. Route waypoints & corridor weather (Concurrent) ────────────────────
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
        except Exception as wp_err:
            logger.warning("Failed fetching route waypoints: %s", wp_err)

    # ── 9. Compute Deterministic Travel Weather Card Risk ────────────────────
    travel_card: Optional[TravelCardData] = None
    if destination_resolved and destination_weather:
        try:
            travel_card = compute_travel_risk(
                origin_name=resolved.name,
                origin_weather=weather_data,
                destination_name=destination_resolved.name,
                destination_weather=destination_weather,
                waypoints=route_waypoints or [],
                cargo=conversation_state.cargo or cargo,
                departure_timing=conversation_state.date_time or departure_timing,
            )
            if travel_card:
                conversation_state.previous_recommendation = travel_card.recommendation
        except Exception as tc_err:
            logger.warning("Failed to compute TravelCardData: %s", tc_err)

    # ── 10. Forecast and tonight summaries ───────────────────────────────────
    is_forecast = _is_forecast_question(body.message)
    forecast_summary = None
    destination_forecast_summary = None
    tonight_summary = None
    destination_tonight_summary = None

    if is_forecast:
        try:
            from app.services.weather_service import get_forecast
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
            logger.warning("Could not fetch 7-day forecast: %s", f_err)

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
                logger.warning("Could not fetch destination forecast: %s", f_err)

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

    # ── 11. Alert suggestion & Single LLM completion call ────────────────────
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

        # Build history for LLM
        llm_history = history_messages if history_messages else None

        # Exactly ONE LLM completion call in the normal chat path
        raw_answer = await generate_weather_response(
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
            conversation_state=conversation_state,
        )

        clean_answer, llm_state_updates = extract_and_validate_llm_state_updates(raw_answer)
        answer = clean_answer

        # Validate and merge any LLM-emitted state updates (Never blindly trust LLM state)
        if llm_state_updates:
            conversation_state = merge_conversation_state(conversation_state, llm_state_updates)

    except httpx.TimeoutException:
        logger.warning("LLM timed out; returning structured fallback for '%s'", resolved.name)
        answer = _build_fallback_answer(weather_data, destination_weather)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM/alert error: %s", exc)
        answer = _build_fallback_answer(weather_data, destination_weather)

    # ── 12. Synchronous State Persistence (Requirement 10) ───────────────────
    if conversation_id:
        await save_conversation_state(conversation_id, user_id, conversation_state)

    # ── 13. Asynchronous Message Persistence & Summarization (Requirement 10 & 11) ─
    if auth_user and conversation_id and answer:
        background_tasks.add_task(
            _persist_turn_and_summarize,
            conversation_id,
            user_id,
            body.message,
            answer,
            conversation_summary,
            history_messages,
        )

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
        conversation_state=conversation_state,
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

    from app.services.conversation_service import _fetch_conversation, get_conversation_context_and_state
    conv = await _fetch_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    summary, recent, state = await get_conversation_context_and_state(conversation_id, user_id)
    return ConversationDetail(
        conversation=conv,
        recent_messages=recent,
        state=state,
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
