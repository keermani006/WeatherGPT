"""
app/services/weather_service.py

Fetch meteorological data from Open-Meteo and return normalised Pydantic models.

Phase 2 additions:
  - TTL caching via app.core.cache (5-min current, 10-min forecast, 5-min hourly)
  - Retry with exponential backoff on transient failures (3 attempts)
  - Circuit breaker to stop hammering Open-Meteo when it's down
"""

import logging
from datetime import datetime
from typing import List, Optional

import httpx

from app.core.cache import (
    current_weather_key,
    forecast_key,
    hourly_key,
    weather_cache,
)
from app.core.config import get_settings
from app.core.resilience import ServiceUnavailableError, async_retry, cb_open_meteo
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
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def _parse_current_weather(data: dict, location_name: str) -> WeatherData:
    current = data.get("current")
    if not current:
        raise ValueError("Invalid Open-Meteo response: missing 'current' weather block")
    code = current.get("weather_code", 0)
    return WeatherData(
        location=location_name,
        temperature=round(float(current["temperature_2m"]), 1),
        feels_like=round(float(current.get("apparent_temperature", current["temperature_2m"])), 1),
        condition=WMO_WEATHER_CODES.get(code, "Clear sky"),
        humidity=int(current.get("relative_humidity_2m", 0)),
        wind_speed=round(float(current.get("wind_speed_10m", 0.0)), 1),
        rain_probability=None,
        rainfall=None,
        forecast_date=None,
    )


def _parse_tomorrow_weather(data: dict, location_name: str) -> WeatherData:
    daily = data.get("daily")
    if not daily or "time" not in daily or len(daily["time"]) < 2:
        raise ValueError("Invalid Open-Meteo response: missing daily forecast for tomorrow")
    idx = 1
    t_max = daily["temperature_2m_max"][idx]
    t_min = daily["temperature_2m_min"][idx]
    code = daily["weather_code"][idx]
    rain_prob = None
    if "precipitation_probability_max" in daily and daily["precipitation_probability_max"][idx] is not None:
        rain_prob = float(daily["precipitation_probability_max"][idx])
    rainfall = None
    if "precipitation_sum" in daily and daily["precipitation_sum"][idx] is not None:
        rainfall = round(float(daily["precipitation_sum"][idx]), 2)
    wind = 0.0
    if "wind_speed_10m_max" in daily and daily["wind_speed_10m_max"][idx] is not None:
        wind = round(float(daily["wind_speed_10m_max"][idx]), 1)
    return WeatherData(
        location=location_name,
        temperature=round((t_max + t_min) / 2.0, 1),
        feels_like=round((t_max + t_min) / 2.0, 1),
        condition=WMO_WEATHER_CODES.get(code, "Partly cloudy"),
        humidity=65,
        wind_speed=wind,
        rain_probability=rain_prob,
        rainfall=rainfall,
        forecast_date=daily["time"][idx],
    )


async def _fetch_current_from_api(latitude: float, longitude: float) -> dict:
    """Raw Open-Meteo request for current weather (used by cache layer)."""
    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "hourly": "precipitation_probability",
        "forecast_days": 1,
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }

    async def _do_request():
        async with cb_open_meteo:
            async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
                resp = await client.get(endpoint, params=params)
                resp.raise_for_status()
                return resp.json()

    return await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )


async def get_current_weather(
    latitude: float,
    longitude: float,
    location_name: str = "Unknown",
) -> CurrentWeatherResponse:
    """Fetch normalised current weather with caching and retry."""
    cache_key = current_weather_key(latitude, longitude)

    async def _fetch():
        logger.info(
            "Open-Meteo current weather fetch: '%s' (%.4f, %.4f)",
            location_name, latitude, longitude,
        )
        data = await _fetch_current_from_api(latitude, longitude)
        current = data.get("current")
        if not current:
            raise ValueError("Invalid Open-Meteo response: missing 'current' block")
        code = current.get("weather_code", 0)
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
            condition=WMO_WEATHER_CODES.get(code, "Clear sky"),
        )
        return CurrentWeatherResponse(
            location=LocationInfo(name=location_name, latitude=latitude, longitude=longitude),
            current=conditions,
            updated_at=current.get("time", datetime.utcnow().isoformat()),
        )

    return await weather_cache.get_or_set(cache_key, _fetch, settings.weather_cache_ttl)


async def get_weather(
    latitude: float, longitude: float, location_name: str, for_tomorrow: bool = False,
) -> WeatherData:
    """Fetch weather for chat pipeline with caching."""
    endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"

    if not for_tomorrow:
        cache_key = current_weather_key(latitude, longitude)
        params = {
            "latitude": latitude, "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "wind_speed_unit": "ms", "timezone": "auto",
        }
        ttl = settings.weather_cache_ttl
    else:
        cache_key = forecast_key(latitude, longitude, 1)
        params = {
            "latitude": latitude, "longitude": longitude,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
            "wind_speed_unit": "ms", "timezone": "auto",
        }
        ttl = settings.forecast_cache_ttl

    cached = weather_cache.get(cache_key + ":chat")
    if cached is not None:
        return cached

    async def _do_request():
        async with cb_open_meteo:
            async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
                resp = await client.get(endpoint, params=params)
                resp.raise_for_status()
                return resp.json()

    logger.info("Fetching weather for '%s' (tomorrow=%s)", location_name, for_tomorrow)
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )
    result = (
        _parse_tomorrow_weather(data, location_name)
        if for_tomorrow
        else _parse_current_weather(data, location_name)
    )
    weather_cache.set(cache_key + ":chat", result, ttl)
    return result


async def get_forecast(
    latitude: float, longitude: float, location_name: str, days: int = 7,
) -> ForecastResponse:
    """Fetch daily forecast with caching and retry."""
    if not 1 <= days <= 16:
        raise ValueError("days must be between 1 and 16")

    cache_key = forecast_key(latitude, longitude, days)

    async def _fetch():
        endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
        params = {
            "latitude": latitude, "longitude": longitude,
            "forecast_days": days,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
            "timezone": "auto",
        }

        async def _do_req():
            async with cb_open_meteo:
                async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
                    resp = await client.get(endpoint, params=params)
                    resp.raise_for_status()
                    return resp.json()

        logger.info("Fetching %d-day forecast for '%s'", days, location_name)
        data = await async_retry(
            _do_req, max_attempts=settings.retry_max_attempts, base_delay=settings.retry_base_delay
        )
        daily = data.get("daily")
        if not daily or "time" not in daily:
            raise ValueError("Invalid Open-Meteo response: missing 'daily' block")

        forecast_list: List[DayForecast] = []
        times = daily["time"]
        for i in range(len(times)):
            code = daily["weather_code"][i] if "weather_code" in daily else 0
            rain_prob = None
            if "precipitation_probability_max" in daily and daily["precipitation_probability_max"][i] is not None:
                rain_prob = int(daily["precipitation_probability_max"][i])
            precip = 0.0
            if "precipitation_sum" in daily and daily["precipitation_sum"][i] is not None:
                precip = round(float(daily["precipitation_sum"][i]), 1)
            wind = 0.0
            if "wind_speed_10m_max" in daily and daily["wind_speed_10m_max"][i] is not None:
                wind = round(float(daily["wind_speed_10m_max"][i]), 1)
            forecast_list.append(DayForecast(
                date=times[i],
                temperature_max=round(float(daily["temperature_2m_max"][i]), 1),
                temperature_min=round(float(daily["temperature_2m_min"][i]), 1),
                rain_probability=rain_prob,
                precipitation=precip,
                wind_speed=wind,
                condition=WMO_WEATHER_CODES.get(code, "Clear sky"),
            ))

        return ForecastResponse(
            location=LocationInfo(name=location_name, latitude=latitude, longitude=longitude),
            forecast=forecast_list,
        )

    return await weather_cache.get_or_set(cache_key, _fetch, settings.forecast_cache_ttl)


async def get_hourly_forecast(
    latitude: float, longitude: float, location_name: str, date_str: str,
) -> HourlyResponse:
    """Fetch 24-hour forecast with caching and retry."""
    cache_key = hourly_key(latitude, longitude, date_str)

    async def _fetch():
        endpoint = f"{settings.open_meteo_base_url.rstrip('/')}/forecast"
        params = {
            "latitude": latitude, "longitude": longitude,
            "start_date": date_str, "end_date": date_str,
            "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m",
            "timezone": "auto",
        }

        async def _do_req():
            async with cb_open_meteo:
                async with httpx.AsyncClient(timeout=settings.weather_api_timeout) as client:
                    resp = await client.get(endpoint, params=params)
                    resp.raise_for_status()
                    return resp.json()

        logger.info("Fetching hourly forecast for '%s' on %s", location_name, date_str)
        data = await async_retry(
            _do_req, max_attempts=settings.retry_max_attempts, base_delay=settings.retry_base_delay
        )
        hourly = data.get("hourly")
        if not hourly or "time" not in hourly:
            raise ValueError("Invalid Open-Meteo response: missing 'hourly' block")

        entries: List[HourlyEntry] = []
        for i, iso_time in enumerate(hourly["time"]):
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
            entries.append(HourlyEntry(
                time=time_part,
                temperature=round(float(hourly["temperature_2m"][i]), 1),
                rain_probability=rain_prob,
                precipitation=precip,
                wind_speed=wind,
            ))

        return HourlyResponse(
            location=LocationInfo(name=location_name, latitude=latitude, longitude=longitude),
            date=date_str,
            hourly=entries,
        )

    return await weather_cache.get_or_set(cache_key, _fetch, settings.hourly_cache_ttl)
