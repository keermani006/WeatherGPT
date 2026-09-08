"""
app/api/routes/weather.py

FastAPI endpoints for direct meteorological data:
  - GET /api/v1/weather/current
  - GET /api/v1/weather/forecast
  - GET /api/v1/weather/hourly
"""

import logging
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.weather import CurrentWeatherResponse, ForecastResponse, HourlyResponse
from app.services.location_service import reverse_geocode
from app.services.weather_service import (
    get_current_weather,
    get_forecast,
    get_hourly_forecast,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/weather", tags=["Weather"])


@router.get(
    "/current",
    response_model=CurrentWeatherResponse,
    summary="Get current weather conditions",
    description="Fetch current weather conditions for given GPS coordinates via Open-Meteo.",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid coordinates supplied."},
        502: {"model": ErrorResponse, "description": "Weather provider unavailable."},
    },
)
async def current_weather_endpoint(
    latitude: float = Query(..., ge=-90.0, le=90.0, description="Latitude coordinate."),
    longitude: float = Query(..., ge=-180.0, le=180.0, description="Longitude coordinate."),
):
    try:
        location_name = await reverse_geocode(latitude, longitude)
        return await get_current_weather(latitude, longitude, location_name)
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.error("Open-Meteo current weather error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "WEATHER_SERVICE_UNAVAILABLE", "message": "Weather provider unavailable."}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_REQUEST", "message": str(exc)}},
        ) from exc


@router.get(
    "/forecast",
    response_model=ForecastResponse,
    summary="Get daily multi-day forecast",
    description="Fetch daily summary forecast (1 to 16 days) for given coordinates via Open-Meteo.",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid parameters or days out of range."},
        502: {"model": ErrorResponse, "description": "Weather provider unavailable."},
    },
)
async def forecast_endpoint(
    latitude: float = Query(..., ge=-90.0, le=90.0, description="Latitude coordinate."),
    longitude: float = Query(..., ge=-180.0, le=180.0, description="Longitude coordinate."),
    days: int = Query(7, ge=1, le=16, description="Number of forecast days (1-16)."),
):
    try:
        location_name = await reverse_geocode(latitude, longitude)
        return await get_forecast(latitude, longitude, location_name, days=days)
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.error("Open-Meteo forecast error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "WEATHER_SERVICE_UNAVAILABLE", "message": "Weather provider unavailable."}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_REQUEST", "message": str(exc)}},
        ) from exc


@router.get(
    "/hourly",
    response_model=HourlyResponse,
    summary="Get hourly forecast for a date",
    description="Fetch 24-hour forecast breakdown for a specific date (YYYY-MM-DD) via Open-Meteo.",
    responses={
        400: {"model": ErrorResponse, "description": "Date outside supported range or invalid format."},
        502: {"model": ErrorResponse, "description": "Weather provider unavailable."},
    },
)
async def hourly_forecast_endpoint(
    latitude: float = Query(..., ge=-90.0, le=90.0, description="Latitude coordinate."),
    longitude: float = Query(..., ge=-180.0, le=180.0, description="Longitude coordinate."),
    date: str = Query(..., description="Target date in YYYY-MM-DD format."),
):
    # Validate date format and forecast range
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_DATE_FORMAT", "message": "Date must be in YYYY-MM-DD format."}},
        )

    today = datetime.now(timezone.utc).date()
    max_forecast_date = today + timedelta(days=15)

    if target_date < today - timedelta(days=2) or target_date > max_forecast_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "DATE_OUT_OF_RANGE",
                    "message": f"Requested date must be between {today.isoformat()} and {max_forecast_date.isoformat()}.",
                }
            },
        )

    try:
        location_name = await reverse_geocode(latitude, longitude)
        return await get_hourly_forecast(latitude, longitude, location_name, date_str=date)
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.error("Open-Meteo hourly forecast error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "WEATHER_SERVICE_UNAVAILABLE", "message": "Weather provider unavailable."}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_REQUEST", "message": str(exc)}},
        ) from exc
