"""
app/services/climate_service.py

Service layer for querying the Open-Meteo Climate API (CMIP6 climate models).

Phase 2 additions:
  - TTL caching (1 hour — climate data is effectively static)
  - Retry with exponential backoff
  - Circuit breaker on climate API
"""

import logging
from typing import List, Optional

import httpx

from app.core.cache import climate_cache, climate_key
from app.core.config import get_settings
from app.core.resilience import async_retry, cb_climate
from app.schemas.climate import (
    ClimateDailyPoint,
    ClimateDataSource,
    ClimateLocation,
    ClimatePeriod,
    ClimateResponse,
    ClimateSummary,
    ClimateTrend,
)

logger = logging.getLogger(__name__)

DEFAULT_CLIMATE_MODEL = "CMCC_CM2_VHR4"
DEFAULT_START_DATE = "2025-01-01"
DEFAULT_END_DATE = "2025-01-30"

# Supported CMIP6 models accepted by Open-Meteo Climate API
SUPPORTED_CLIMATE_MODELS = {
    "CMCC_CM2_VHR4",
    "FGOALS_f3_H",
    "HiRAM_SIT_HR",
    "MRI_AGCM3_2_S",
    "EC_Earth3P_HR",
    "MPI_ESM1_2_XR",
    "NICAM16_8S",
}


def _calculate_summary(daily_points: List[ClimateDailyPoint]) -> ClimateSummary:
    temps = [p.temperature_mean for p in daily_points if p.temperature_mean is not None]
    precips = [p.precipitation for p in daily_points if p.precipitation is not None]
    humidities = [p.humidity for p in daily_points if p.humidity is not None]
    winds = [p.wind_speed for p in daily_points if p.wind_speed is not None]
    return ClimateSummary(
        average_temperature=round(sum(temps) / len(temps), 1) if temps else None,
        total_precipitation=round(sum(precips), 1) if precips else None,
        average_humidity=round(sum(humidities) / len(humidities), 1) if humidities else None,
        average_wind_speed=round(sum(winds) / len(winds), 1) if winds else None,
    )


def _calculate_trend(daily_points: List[ClimateDailyPoint]) -> Optional[ClimateTrend]:
    temps = [p.temperature_mean for p in daily_points if p.temperature_mean is not None]
    precips = [p.precipitation for p in daily_points if p.precipitation is not None]
    if len(temps) < 6:
        return None
    mid = len(temps) // 2
    diff_t = (sum(temps[mid:]) / (len(temps) - mid)) - (sum(temps[:mid]) / mid)
    temp_trend = "increasing" if diff_t > 0.5 else ("decreasing" if diff_t < -0.5 else "stable")
    precip_trend = "stable"
    if len(precips) >= 6:
        diff_p = (sum(precips[mid:]) / (len(precips) - mid)) - (sum(precips[:mid]) / mid)
        precip_trend = "increasing" if diff_p > 0.5 else ("decreasing" if diff_p < -0.5 else "stable")
    return ClimateTrend(temperature=temp_trend, precipitation=precip_trend)


async def get_climate_data(
    latitude: float,
    longitude: float,
    location_name: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    model: Optional[str] = None,
) -> ClimateResponse:
    """
    Fetch CMIP6 climate projections from Open-Meteo Climate API.
    Results cached for 1 hour (climate data is quasi-static).
    """
    settings = get_settings()
    selected_model = model or DEFAULT_CLIMATE_MODEL
    selected_start = start_date or DEFAULT_START_DATE
    selected_end = end_date or DEFAULT_END_DATE

    cache_key = climate_key(latitude, longitude, selected_start, selected_end, selected_model)
    cached = await climate_cache.get(cache_key)
    if cached is not None:
        return cached

    endpoint = f"{settings.open_meteo_climate_url}/climate"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": selected_start,
        "end_date": selected_end,
        "models": selected_model,
        "daily": (
            "temperature_2m_mean,"
            "temperature_2m_max,"
            "temperature_2m_min,"
            "precipitation_sum,"
            "relative_humidity_2m_mean,"
            "wind_speed_10m_mean"
        ),
    }

    async def _do_request():
        async with cb_climate:
            async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
                resp = await client.get(endpoint, params=params)
                resp.raise_for_status()
                return resp.json()

    logger.info(
        "Querying Open-Meteo Climate API: (%.4f, %.4f), %s to %s, model=%s",
        latitude, longitude, selected_start, selected_end, selected_model,
    )
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )

    daily_raw = data.get("daily", {})
    times = daily_raw.get("time", [])
    temp_means = daily_raw.get("temperature_2m_mean", [])
    temp_maxs = daily_raw.get("temperature_2m_max", [])
    temp_mins = daily_raw.get("temperature_2m_min", [])
    precips = daily_raw.get("precipitation_sum", [])
    humidities = daily_raw.get("relative_humidity_2m_mean", [])
    winds = daily_raw.get("wind_speed_10m_mean", [])

    daily_points: List[ClimateDailyPoint] = []
    for i, date_str in enumerate(times):
        daily_points.append(ClimateDailyPoint(
            date=date_str,
            temperature_mean=(round(temp_means[i], 1) if i < len(temp_means) and temp_means[i] is not None else None),
            temperature_max=(round(temp_maxs[i], 1) if i < len(temp_maxs) and temp_maxs[i] is not None else None),
            temperature_min=(round(temp_mins[i], 1) if i < len(temp_mins) and temp_mins[i] is not None else None),
            precipitation=(round(precips[i], 2) if i < len(precips) and precips[i] is not None else None),
            humidity=(round(humidities[i], 1) if i < len(humidities) and humidities[i] is not None else None),
            wind_speed=(round(winds[i], 1) if i < len(winds) and winds[i] is not None else None),
        ))

    result = ClimateResponse(
        location=ClimateLocation(latitude=latitude, longitude=longitude, name=location_name),
        period=ClimatePeriod(start=selected_start, end=selected_end),
        model=selected_model,
        daily=daily_points,
        summary=_calculate_summary(daily_points),
        trend=_calculate_trend(daily_points),
        data_source=ClimateDataSource(),
    )
    await climate_cache.set(cache_key, result, settings.climate_cache_ttl)
    return result
