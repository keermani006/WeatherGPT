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
    # NOTE: The guardrail runs unconditionally. A location in the message does
    # NOT bypass it — "What's the population of Delhi?" is not weather-related.
    if not is_weather_related(body.message):
        logger.info("Request rejected by guardrail | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    from app.services.location_service import (
        extract_location_from_message,
        extract_locations_from_message,
        extract_route_info,
        extract_travel_destination,
    )
    route_orig, route_dest = extract_route_info(body.message)
    travel_dest_name = route_dest or extract_travel_destination(body.message)
    msg_locs = extract_locations_from_message(body.message)

    # ── 3. Resolve primary location ──────────────────────────────────────────
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

    # ── 4. Resolve secondary/destination location (if any) ───────────────────
    secondary_query = None
    if route_dest:
        secondary_query = route_dest
    elif travel_dest_name:
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

    # ── 5. Fetch weather data ─────────────────────────────────────────────────
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

    # ── 6. Fetch 7-day forecast and tonight condition details ────────────────
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

    # ── 7. Build alert suggestion & generate LLM response ─────────────────────
    alert_suggestion = None
    created_alert = None
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

        # If alert intent detected and request is authenticated, automatically create the alert
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

        answer = await generate_weather_response(
            user_question=body.message,
            weather_data=weather_data,
            history=body.history,
            destination_weather=destination_weather,
            alert_suggestion=alert_suggestion,
            forecast_summary=forecast_summary,
            destination_forecast_summary=destination_forecast_summary,
            tonight_summary=tonight_summary,
            destination_tonight_summary=destination_tonight_summary,
        )
    except httpx.TimeoutException:
        logger.warning("LLM timed out; returning structured fallback for '%s'", resolved.name)
        answer = _build_fallback_answer(weather_data, destination_weather)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM/alert error: %s", exc)
        answer = _build_fallback_answer(weather_data, destination_weather)

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed in %.1f ms | location='%s' | travel=%s | alert=%s | created=%s",
        elapsed, resolved.name, destination_resolved is not None, alert_suggestion is not None, created_alert is not None,
    )

    return ChatResponse(
        answer=answer,
        location=resolved.name,
        weather_data=weather_data,
        destination_weather=destination_weather,
        alert_suggestion=alert_suggestion,
        created_alert=created_alert,
    )
