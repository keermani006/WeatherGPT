"""
app/services/climate_service.py

Service layer for querying the Open-Meteo Climate API (CMIP6 climate models).
Calculates summary statistics, trends, and attaches dataset attribution.
"""

import logging
from typing import List, Optional, Tuple
import httpx

from app.core.config import get_settings
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


def _calculate_summary(daily_points: List[ClimateDailyPoint]) -> ClimateSummary:
    """Calculate average and total statistics across the period from actual data points."""
    temps = [p.temperature_mean for p in daily_points if p.temperature_mean is not None]
    precips = [p.precipitation for p in daily_points if p.precipitation is not None]
    humidities = [p.humidity for p in daily_points if p.humidity is not None]
    winds = [p.wind_speed for p in daily_points if p.wind_speed is not None]

    avg_temp = round(sum(temps) / len(temps), 1) if temps else None
    total_precip = round(sum(precips), 1) if precips else None
    avg_humidity = round(sum(humidities) / len(humidities), 1) if humidities else None
    avg_wind = round(sum(winds) / len(winds), 1) if winds else None

    return ClimateSummary(
        average_temperature=avg_temp,
        total_precipitation=total_precip,
        average_humidity=avg_humidity,
        average_wind_speed=avg_wind,
    )


def _calculate_trend(daily_points: List[ClimateDailyPoint]) -> Optional[ClimateTrend]:
    """
    Calculate qualitative trend (increasing, decreasing, stable)
    if there are at least 6 daily observations.
    """
    temps = [p.temperature_mean for p in daily_points if p.temperature_mean is not None]
    precips = [p.precipitation for p in daily_points if p.precipitation is not None]

    if len(temps) < 6:
        return None

    # Temperature trend: compare second half mean with first half mean
    mid = len(temps) // 2
    first_half_t = sum(temps[:mid]) / mid
    second_half_t = sum(temps[mid:]) / (len(temps) - mid)
    diff_t = second_half_t - first_half_t

    if diff_t > 0.5:
        temp_trend = "increasing"
    elif diff_t < -0.5:
        temp_trend = "decreasing"
    else:
        temp_trend = "stable"

    # Precipitation trend
    first_half_p = sum(precips[:mid]) / mid
    second_half_p = sum(precips[mid:]) / (len(precips) - mid)
    diff_p = second_half_p - first_half_p

    if diff_p > 0.5:
        precip_trend = "increasing"
    elif diff_p < -0.5:
        precip_trend = "decreasing"
    else:
        precip_trend = "stable"

    return ClimateTrend(
        temperature=temp_trend,
        precipitation=precip_trend,
    )


async def get_climate_data(
    latitude: float,
    longitude: float,
    location_name: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    model: Optional[str] = None,
) -> ClimateResponse:
    """
    Fetch CMIP6 climate model projections from Open-Meteo Climate API.
    Computes statistical summaries, trend detection, and attaches attribution.
    """
    settings = get_settings()
    selected_model = model or DEFAULT_CLIMATE_MODEL
    selected_start = start_date or DEFAULT_START_DATE
    selected_end = end_date or DEFAULT_END_DATE

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

    logger.info(
        "Querying Open-Meteo Climate API: coords=(%.4f, %.4f), period=%s to %s, model=%s",
        latitude,
        longitude,
        selected_start,
        selected_end,
        selected_model,
    )

    async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()

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
        daily_points.append(
            ClimateDailyPoint(
                date=date_str,
                temperature_mean=(
                    round(temp_means[i], 1)
                    if i < len(temp_means) and temp_means[i] is not None
                    else None
                ),
                temperature_max=(
                    round(temp_maxs[i], 1)
                    if i < len(temp_maxs) and temp_maxs[i] is not None
                    else None
                ),
                temperature_min=(
                    round(temp_mins[i], 1)
                    if i < len(temp_mins) and temp_mins[i] is not None
                    else None
                ),
                precipitation=(
                    round(precips[i], 2)
                    if i < len(precips) and precips[i] is not None
                    else None
                ),
                humidity=(
                    round(humidities[i], 1)
                    if i < len(humidities) and humidities[i] is not None
                    else None
                ),
                wind_speed=(
                    round(winds[i], 1)
                    if i < len(winds) and winds[i] is not None
                    else None
                ),
            )
        )

    summary = _calculate_summary(daily_points)
    trend = _calculate_trend(daily_points)

    return ClimateResponse(
        location=ClimateLocation(
            latitude=latitude,
            longitude=longitude,
            name=location_name,
        ),
        period=ClimatePeriod(
            start=selected_start,
            end=selected_end,
        ),
        model=selected_model,
        daily=daily_points,
        summary=summary,
        trend=trend,
        data_source=ClimateDataSource(),
    )
