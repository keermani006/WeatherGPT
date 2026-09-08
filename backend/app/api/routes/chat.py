"""
app/api/routes/chat.py

POST /api/v1/chat

Request flow:
  1. Validate request (Pydantic / FastAPI — max_length=1000 enforced on message).
  2. Guardrail check — reject non-weather messages immediately with 403.
  3. Resolve location (explicit → extracted from message → GPS coordinates).
  4. Detect tomorrow question.
  5. Fetch weather from Open-Meteo (cached, with retry).
  6. Ask Groq LLM to explain the weather (with circuit breaker + fallback).
  7. Return structured ChatResponse.
"""

import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.resilience import ServiceUnavailableError
from app.schemas.chat import ChatRequest, ChatResponse, WeatherData
from app.services.llm_service import generate_weather_response
from app.services.location_service import resolve_location
from app.services.weather_service import get_weather
from app.utils.weather_guardrail import is_weather_related

settings = get_settings()

logger = logging.getLogger(__name__)

router = APIRouter()

# Keywords that signal the user is asking about tomorrow.
_TOMORROW_KEYWORDS = ["tomorrow", "next day", "day after today"]


def _is_tomorrow_question(message: str) -> bool:
    lower = message.lower()
    return any(kw in lower for kw in _TOMORROW_KEYWORDS)


def _build_fallback_answer(weather_data: WeatherData) -> str:
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
    lines.append("")
    lines.append("The conversational AI is temporarily unavailable.")
    return "\n".join(lines)


@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a weather question",
    description=(
        "Send a natural-language weather question (max 1000 characters). "
        "The backend resolves location (via query place name or browser GPS), "
        "fetches live weather data from Open-Meteo, "
        "and uses Groq LLM to produce a conversational answer. "
        "Non-weather questions are rejected immediately with 403."
    ),
    responses={
        200: {"description": "Successful weather answer or location prompt."},
        400: {"description": "Empty or invalid message."},
        403: {"description": "Question is not weather-related."},
        404: {"description": "Location not found."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream API error (Open-Meteo / Nominatim / Groq)."},
        503: {"description": "Upstream API temporarily unavailable (timeout)."},
    },
)
@limiter.limit(settings.rate_limit_chat)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Handle a single user weather question end-to-end."""
    start = time.perf_counter()
    logger.info("Request received | message='%s' | location='%s' | coords=(%s, %s)",
                body.message[:80], body.location, body.latitude, body.longitude)

    # ── 1. Guardrail ─────────────────────────────────────────────────────────
    if not is_weather_related(body.message):
        logger.info("Request rejected by guardrail | message='%s'", body.message[:80])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="I can only help with weather-related questions.",
        )

    # ── 2. Resolve location ──────────────────────────────────────────────────
    try:
        resolved = await resolve_location(
            explicit=body.location,
            message=body.message,
            latitude=body.latitude,
            longitude=body.longitude,
        )
    except ValueError as exc:
        logger.warning("Geocoding failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error("Location geocoding service timed out: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Location service is temporarily unavailable. Please try again shortly.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Location geocoding service error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Location geocoding service returned an upstream error.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected location resolution error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Location resolution encountered an unexpected error.",
        ) from exc

    # If neither explicit location nor coordinates are available, prompt user
    if not resolved:
        logger.info("No location provided or resolved; prompting user for location")
        return ChatResponse(
            answer=(
                "I need your location to check the weather. Please mention a city name "
                "(for example, 'What's the weather in Chennai?') or allow browser location access."
            ),
            location="Unknown",
            weather_data=None,
        )

    logger.info("Location resolved: '%s' (lat=%.4f, lon=%.4f, source=%s)",
                resolved.name, resolved.latitude, resolved.longitude, resolved.source)

    # ── 3. Fetch weather data from Open-Meteo ────────────────────────────────
    for_tomorrow = _is_tomorrow_question(body.message)
    try:
        weather_data = await get_weather(
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            location_name=resolved.name,
            for_tomorrow=for_tomorrow,
        )
    except httpx.TimeoutException as exc:
        logger.error("Open-Meteo weather API timed out for '%s'", resolved.name)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather service is temporarily unavailable. Please try again shortly.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Open-Meteo weather API HTTP error %s for '%s'", exc.response.status_code, resolved.name)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Weather service returned an upstream error. Please try again.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected weather service error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Weather service encountered an unexpected error.",
        ) from exc

    logger.info("Weather data fetched for '%s'", resolved.name)

    # ── 4. Generate LLM answer via Groq ──────────────────────────────────────
    try:
        answer = await generate_weather_response(
            user_question=body.message,
            weather_data=weather_data,
        )
    except httpx.TimeoutException:
        logger.warning("LLM timed out; returning structured fallback for '%s'", resolved.name)
        answer = _build_fallback_answer(weather_data)
    except httpx.HTTPStatusError as exc:
        logger.error("LLM HTTP error %s", exc.response.status_code)
        answer = _build_fallback_answer(weather_data)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected LLM error: %s", exc)
        answer = _build_fallback_answer(weather_data)

    elapsed = (time.perf_counter() - start) * 1000
    logger.info("Request completed in %.1f ms | location='%s'", elapsed, resolved.name)

    return ChatResponse(
        answer=answer,
        location=resolved.name,
        weather_data=weather_data,
    )
