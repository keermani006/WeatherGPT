"""
app/api/routes/chat.py

POST /api/v1/chat

Request flow:
  1. Validate request.
  2. Friendly greeting fast-path.
  3. Guardrail check — reject non-weather messages with 403.
  4. Detect travel intent → resolve BOTH origin + destination.
  5. Resolve primary location (explicit → extracted → GPS).
  6. Detect tomorrow question.
  7. Fetch weather (origin + optional destination) from Open-Meteo.
  8. Run alert intent detection in parallel with LLM answer generation.
  9. Return structured ChatResponse with optional destination_weather + alert_suggestion.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.resilience import ServiceUnavailableError
from app.schemas.chat import AlertSuggestion, ChatRequest, ChatResponse, WeatherData
from app.services.llm_service import build_alert_suggestion, generate_weather_response
from app.services.location_service import (
    extract_travel_destination,
    geocode_location,
    resolve_location,
)
from app.services.weather_service import get_weather
from app.utils.weather_guardrail import is_weather_related

settings = get_settings()

logger = logging.getLogger(__name__)

router = APIRouter()

_TOMORROW_KEYWORDS = ["tomorrow", "next day", "day after today"]


def _is_tomorrow_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TOMORROW_KEYWORDS)


def _build_fallback_answer(weather_data: WeatherData, destination: Optional[WeatherData] = None) -> str:
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


@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a weather question",
    description=(
        "Send a natural-language weather question (max 1000 characters). "
        "Supports conversation history for multi-turn chat, travel queries "
        "(e.g. 'Is it safe to travel to Delhi?'), and alert intent detection. "
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
        "Chat request | message='%s' | location='%s' | coords=(%s, %s) | history_turns=%d",
        body.message[:80], body.location, body.latitude, body.longitude,
        len(body.history) if body.history else 0,
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
    from app.services.location_service import extract_location_from_message
    has_location_intent = bool(
        body.location
        or extract_location_from_message(body.message)
        or extract_travel_destination(body.message)
    )
    if not is_weather_related(body.message) and not has_location_intent:
        logger.info("Request rejected by guardrail | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    # ── 3. Detect travel intent ───────────────────────────────────────────────
    travel_dest_name = extract_travel_destination(body.message)
    logger.info("Travel destination extracted: %s", travel_dest_name)

    # ── 4. Resolve primary location ───────────────────────────────────────────
    try:
        resolved = await resolve_location(
            explicit=body.location,
            message=body.message,
            latitude=body.latitude,
            longitude=body.longitude,
        )
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
        )

    logger.info("Primary location resolved: '%s' (source=%s)", resolved.name, resolved.source)

    # ── 5. Resolve travel destination (if any) ────────────────────────────────
    destination_resolved = None
    if travel_dest_name:
        try:
            dest_lat, dest_lon, dest_name = await geocode_location(travel_dest_name)
            from app.services.location_service import ResolvedLocation
            destination_resolved = ResolvedLocation(
                name=dest_name,
                latitude=dest_lat,
                longitude=dest_lon,
                source="extracted",
            )
            logger.info("Travel destination resolved: '%s'", dest_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resolve travel destination '%s': %s", travel_dest_name, exc)

    # ── 6. Fetch weather data ─────────────────────────────────────────────────
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

    # ── 7. Fetch destination weather (travel queries) ─────────────────────────
    destination_weather: Optional[WeatherData] = None
    if destination_resolved:
        try:
            destination_weather = await get_weather(
                latitude=destination_resolved.latitude,
                longitude=destination_resolved.longitude,
                location_name=destination_resolved.name,
                for_tomorrow=for_tomorrow,
            )
            logger.info("Destination weather fetched for '%s'", destination_resolved.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch destination weather: %s", exc)

    # ── 8. Build alert suggestion & generate LLM response ─────────────────────
    alert_suggestion = None
    try:
        raw_alert = await build_alert_suggestion(body.message, weather_data)
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
            alert_suggestion = AlertSuggestion(
                location_name=resolved.name,
                latitude=resolved.latitude,
                longitude=resolved.longitude,
                condition=raw_alert.condition,
                threshold=raw_alert.threshold,
                description=raw_alert.description,
            )

        answer = await generate_weather_response(
            user_question=body.message,
            weather_data=weather_data,
            history=body.history,
            destination_weather=destination_weather,
            alert_suggestion=alert_suggestion,
        )
    except httpx.TimeoutException:
        logger.warning("LLM timed out; returning structured fallback for '%s'", resolved.name)
        answer = _build_fallback_answer(weather_data, destination_weather)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM/alert error: %s", exc)
        answer = _build_fallback_answer(weather_data, destination_weather)

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed in %.1f ms | location='%s' | travel=%s | alert=%s",
        elapsed, resolved.name, destination_resolved is not None, alert_suggestion is not None,
    )

    return ChatResponse(
        answer=answer,
        location=resolved.name,
        weather_data=weather_data,
        destination_weather=destination_weather,
        alert_suggestion=alert_suggestion,
    )
