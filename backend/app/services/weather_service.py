"""
app/services/weather_service.py

Single responsibility: fetch meteorological data from Open-Meteo and
return it as our normalised Pydantic models.

Provider: Open-Meteo (Free tier, no API key required).
Accepts: latitude and longitude coordinates.

Public API:
──────────
    get_weather(...) -> WeatherData (for chat)
    get_current_weather(...) -> CurrentWeatherResponse
    get_forecast(...) -> ForecastResponse
    get_hourly_forecast(...) -> HourlyResponse
"""

import logging
from datetime import datetime
from typing import List, Optional

import httpx

from app.core.config import get_settings
from app.schemas.chat import WeatherData
from app.schemas.weather import (
    CurrentConditions,
    CurrentWeatherResponse,
    DayForecast,
    ForecastResponse,
    HourlyEntry,
    HourlyResponse,
    LocationInfo,
)

logger = logging.getLogger(__name__)
settings = get_settings()

WMO_WEATHER_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _parse_current_weather(data: dict, location_name: str) -> WeatherData:
    """Parse Open-Meteo current weather payload into normalized WeatherData."""
    current = data.get("current")
    if not current:
        raise ValueError("Invalid Open-Meteo response: missing 'current' weather block")

    code = current.get("weather_code", 0)
    condition = WMO_WEATHER_CODES.get(code, "Clear sky")

    return WeatherData(
        location=location_name,
        temperature=round(float(current["temperature_2m"]), 1),
        feels_like=round(float(current.get("apparent_temperature", current["temperature_2m"])), 1),
        condition=condition,
        humidity=int(current.get("relative_humidity_2m", 0)),
        wind_speed=round(float(current.get("wind_speed_10m", 0.0)), 1),
        rain_probability=None,
        rainfall=None,
        forecast_date=None,
    )


def _parse_tomorrow_weather(data: dict, location_name: str) -> WeatherData:
    """Parse Open-Meteo daily forecast payload for tomorrow into normalized WeatherData."""
    daily = data.get("daily")
    if not daily or "time" not in daily or len(daily["time"]) < 2:
        raise ValueError("Invalid Open-Meteo response: missing daily forecast for tomorrow")

    target_idx = 1  # Index 0 is today, Index 1 is tomorrow
    target_date = daily["time"][target_idx]

    t_max = daily["temperature_2m_max"][target_idx]
    t_min = daily["temperature_2m_min"][target_idx]
    avg_temp = round((t_max + t_min) / 2.0, 1)

    code = daily["weather_code"][target_idx]
    condition = WMO_WEATHER_CODES.get(code, "Partly cloudy")

    rain_prob = None
    if "precipitation_probability_max" in daily and daily["precipitation_probability_max"][target_idx] is not None:
        rain_prob = float(daily["precipitation_probability_max"][target_idx])

    rainfall = None
    if "precipitation_sum" in daily and daily["precipitation_sum"][target_idx] is not None:
        rainfall = round(float(daily["precipitation_sum"][target_idx]), 2)

    wind_speed = 0.0
    if "wind_speed_10m_max" in daily and daily["wind_speed_10m_max"][target_idx] is not None:
        wind_speed = round(float(daily["wind_speed_10m_max"][target_idx]), 1)

    return WeatherData(
        location=location_name,
        temperature=avg_temp,
        feels_like=avg_temp,
        condition=condition,
        humidity=65,  # Estimated daily average
        wind_speed=wind_speed,
        rain_probability=rain_prob,
        rainfall=rainfall,
        forecast_date=target_date,
    )


async def get_weather(
    latitude: float,
    longitude: float,
    location_name: str,
    for_tomorrow: bool = False,
) -> WeatherData:
    """
    Fetch weather data from Open-Meteo for given coordinates (used by Chat pipeline).
    """
    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"

    if not for_tomorrow:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "wind_speed_unit": "ms",
            "timezone": "auto",
        }
    else:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
            "wind_speed_unit": "ms",
            "timezone": "auto",
        }

    logger.info(
        "Fetching weather from Open-Meteo for '%s' (lat=%.4f, lon=%.4f, tomorrow=%s)",
        location_name, latitude, longitude, for_tomorrow,
    )

    async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
        resp = await client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()

    if not for_tomorrow:
        weather_data = _parse_current_weather(data, location_name)
    else:
        weather_data = _parse_tomorrow_weather(data, location_name)

    logger.info("Weather data successfully retrieved from Open-Meteo for '%s'", location_name)
    return weather_data


async def get_current_weather(
    latitude: float,
    longitude: float,
    location_name: str = "Unknown",
) -> CurrentWeatherResponse:
    """
    Fetch normalized current weather conditions from Open-Meteo.
    """
    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "hourly": "precipitation_probability",
        "forecast_days": 1,
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
        resp = await client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()

    current = data.get("current")
    if not current:
        raise ValueError("Invalid Open-Meteo response: missing 'current' block")

    code = current.get("weather_code", 0)
    condition = WMO_WEATHER_CODES.get(code, "Clear sky")

    # Find precipitation probability for current hour from hourly array if available
    rain_prob = None
    hourly = data.get("hourly", {})
    if "time" in hourly and "precipitation_probability" in hourly:
        curr_time = current.get("time", "")
        hour_prefix = curr_time[:13]
        for idx, t in enumerate(hourly["time"]):
            if t.startswith(hour_prefix) and idx < len(hourly["precipitation_probability"]):
                val = hourly["precipitation_probability"][idx]
                if val is not None:
                    rain_prob = int(val)
                break

    conditions = CurrentConditions(
        temperature=round(float(current["temperature_2m"]), 1),
        feels_like=round(float(current.get("apparent_temperature", current["temperature_2m"])), 1),
        humidity=int(current.get("relative_humidity_2m", 0)),
        wind_speed=round(float(current.get("wind_speed_10m", 0.0)), 1),
        precipitation=round(float(current.get("precipitation", 0.0)), 1),
        rain_probability=rain_prob,
        condition=condition,
    )

    updated_at = current.get("time", datetime.utcnow().isoformat())

    return CurrentWeatherResponse(
        location=LocationInfo(
            name=location_name,
            latitude=latitude,
            longitude=longitude,
        ),
        current=conditions,
        updated_at=updated_at,
    )


async def get_forecast(
    latitude: float,
    longitude: float,
    location_name: str,
    days: int = 7,
) -> ForecastResponse:
    """
    Fetch daily forecast for 1-16 days from Open-Meteo.
    """
    if not 1 <= days <= 16:
        raise ValueError("days must be between 1 and 16")

    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "forecast_days": days,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
        resp = await client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()

    daily = data.get("daily")
    if not daily or "time" not in daily:
        raise ValueError("Invalid Open-Meteo response: missing 'daily' forecast block")

    forecast_list: List[DayForecast] = []
    times = daily["time"]
    for i in range(len(times)):
        code = daily["weather_code"][i] if "weather_code" in daily else 0
        cond = WMO_WEATHER_CODES.get(code, "Clear sky")

        rain_prob = None
        if "precipitation_probability_max" in daily and daily["precipitation_probability_max"][i] is not None:
            rain_prob = int(daily["precipitation_probability_max"][i])

        precip = 0.0
        if "precipitation_sum" in daily and daily["precipitation_sum"][i] is not None:
            precip = round(float(daily["precipitation_sum"][i]), 1)

        wind = 0.0
        if "wind_speed_10m_max" in daily and daily["wind_speed_10m_max"][i] is not None:
            wind = round(float(daily["wind_speed_10m_max"][i]), 1)

        t_max = round(float(daily["temperature_2m_max"][i]), 1)
        t_min = round(float(daily["temperature_2m_min"][i]), 1)

        forecast_list.append(
            DayForecast(
                date=times[i],
                temperature_max=t_max,
                temperature_min=t_min,
                rain_probability=rain_prob,
                precipitation=precip,
                wind_speed=wind,
                condition=cond,
            )
        )

    return ForecastResponse(
        location=LocationInfo(
            name=location_name,
            latitude=latitude,
            longitude=longitude,
        ),
        forecast=forecast_list,
    )


async def get_hourly_forecast(
    latitude: float,
    longitude: float,
    location_name: str,
    date_str: str,
) -> HourlyResponse:
    """
    Fetch 24-hour forecast for a specific date from Open-Meteo.
    """
    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m",
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
        resp = await client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()

    hourly = data.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("Invalid Open-Meteo response: missing 'hourly' block")

    entries: List[HourlyEntry] = []
    times = hourly["time"]
    for i in range(len(times)):
        iso_time = times[i]
        # format time as HH:MM
        time_part = iso_time.split("T")[1][:5] if "T" in iso_time else iso_time[-5:]

        rain_prob = None
        if "precipitation_probability" in hourly and hourly["precipitation_probability"][i] is not None:
            rain_prob = int(hourly["precipitation_probability"][i])

        precip = 0.0
        if "precipitation" in hourly and hourly["precipitation"][i] is not None:
            precip = round(float(hourly["precipitation"][i]), 1)

        wind = 0.0
        if "wind_speed_10m" in hourly and hourly["wind_speed_10m"][i] is not None:
            wind = round(float(hourly["wind_speed_10m"][i]), 1)

        temp = round(float(hourly["temperature_2m"][i]), 1)

        entries.append(
            HourlyEntry(
                time=time_part,
                temperature=temp,
                rain_probability=rain_prob,
                precipitation=precip,
                wind_speed=wind,
            )
        )

    return HourlyResponse(
        location=LocationInfo(
            name=location_name,
            latitude=latitude,
            longitude=longitude,
        ),
        date=date_str,
        hourly=entries,
    )
