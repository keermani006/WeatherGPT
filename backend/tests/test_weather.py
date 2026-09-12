"""
tests/test_weather.py

Unit and integration tests for /api/v1/weather/* endpoints:
  - GET /api/v1/weather/current
  - GET /api/v1/weather/forecast
  - GET /api/v1/weather/hourly
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.weather import (
    CurrentConditions,
    CurrentWeatherResponse,
    DayForecast,
    ForecastResponse,
    HourlyEntry,
    HourlyResponse,
    LocationInfo,
)

client = TestClient(app)

_MOCK_LOCATION_INFO = LocationInfo(name="Hyderabad", latitude=17.385, longitude=78.4867)

_MOCK_CURRENT_RESP = CurrentWeatherResponse(
    location=_MOCK_LOCATION_INFO,
    current=CurrentConditions(
        temperature=30.2,
        feels_like=32.1,
        humidity=68,
        wind_speed=14.5,
        precipitation=0.0,
        rain_probability=20,
        condition="Partly cloudy",
    ),
    updated_at="2026-09-08T15:20:00",
)

_MOCK_FORECAST_RESP = ForecastResponse(
    location=_MOCK_LOCATION_INFO,
    forecast=[
        DayForecast(
            date="2026-09-08",
            temperature_max=32.0,
            temperature_min=24.0,
            rain_probability=65,
            precipitation=8.2,
            wind_speed=18.0,
            condition="Rain",
        ),
        DayForecast(
            date="2026-09-09",
            temperature_max=31.0,
            temperature_min=23.0,
            rain_probability=40,
            precipitation=2.0,
            wind_speed=15.0,
            condition="Partly cloudy",
        ),
    ],
)

_MOCK_HOURLY_RESP = HourlyResponse(
    location=_MOCK_LOCATION_INFO,
    date="2026-09-08",
    hourly=[
        HourlyEntry(
            time="09:00",
            temperature=27.0,
            rain_probability=20,
            precipitation=0.0,
            wind_speed=12.0,
        ),
        HourlyEntry(
            time="10:00",
            temperature=28.5,
            rain_probability=35,
            precipitation=0.2,
            wind_speed=14.0,
        ),
    ],
)


# ── Current Weather Tests ──────────────────────────────────────────────────

class TestCurrentWeather:
    def test_current_weather_valid_coords(self):
        with (
            patch("app.api.routes.weather.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("app.api.routes.weather.get_current_weather", AsyncMock(return_value=_MOCK_CURRENT_RESP)),
        ):
            resp = client.get("/api/v1/weather/current?latitude=17.385&longitude=78.4867")
        assert resp.status_code == 200
        data = resp.json()
        assert data["location"]["name"] == "Hyderabad"
        assert data["current"]["temperature"] == 30.2
        assert data["current"]["condition"] == "Partly cloudy"
        assert "rain_probability" in data["current"]

    def test_current_weather_missing_coords(self):
        resp = client.get("/api/v1/weather/current")
        assert resp.status_code == 422

    def test_current_weather_invalid_latitude(self):
        resp = client.get("/api/v1/weather/current?latitude=100.0&longitude=78.4867")
        assert resp.status_code == 422

    def test_current_weather_upstream_timeout(self):
        with (
            patch("app.api.routes.weather.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("app.api.routes.weather.get_current_weather", AsyncMock(side_effect=httpx.TimeoutException("timeout"))),
        ):
            resp = client.get("/api/v1/weather/current?latitude=17.385&longitude=78.4867")
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "WEATHER_SERVICE_UNAVAILABLE"


# ── Forecast Tests ─────────────────────────────────────────────────────────

class TestForecast:
    def test_forecast_default_days(self):
        with (
            patch("app.api.routes.weather.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("app.api.routes.weather.get_forecast", AsyncMock(return_value=_MOCK_FORECAST_RESP)),
        ):
            resp = client.get("/api/v1/weather/forecast?latitude=17.385&longitude=78.4867")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["forecast"]) == 2
        assert data["forecast"][0]["temperature_max"] == 32.0

    def test_forecast_custom_days(self):
        with (
            patch("app.api.routes.weather.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("app.api.routes.weather.get_forecast", AsyncMock(return_value=_MOCK_FORECAST_RESP)) as mock_fn,
        ):
            resp = client.get("/api/v1/weather/forecast?latitude=17.385&longitude=78.4867&days=3")
        assert resp.status_code == 200
        mock_fn.assert_called_once_with(17.385, 78.4867, "Hyderabad", days=3)

    def test_forecast_invalid_days_too_high(self):
        resp = client.get("/api/v1/weather/forecast?latitude=17.385&longitude=78.4867&days=20")
        assert resp.status_code == 422

    def test_forecast_invalid_days_zero(self):
        resp = client.get("/api/v1/weather/forecast?latitude=17.385&longitude=78.4867&days=0")
        assert resp.status_code == 422


# ── Hourly Tests ───────────────────────────────────────────────────────────

class TestHourlyForecast:
    def test_hourly_valid_date(self):
        today_str = datetime.now(timezone.utc).date().isoformat()
        mock_resp = HourlyResponse(
            location=_MOCK_LOCATION_INFO,
            date=today_str,
            hourly=_MOCK_HOURLY_RESP.hourly,
        )
        with (
            patch("app.api.routes.weather.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("app.api.routes.weather.get_hourly_forecast", AsyncMock(return_value=mock_resp)),
        ):
            resp = client.get(f"/api/v1/weather/hourly?latitude=17.385&longitude=78.4867&date={today_str}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == today_str
        assert len(data["hourly"]) == 2
        assert data["hourly"][0]["time"] == "09:00"

    def test_hourly_invalid_date_format(self):
        resp = client.get("/api/v1/weather/hourly?latitude=17.385&longitude=78.4867&date=08-09-2026")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_DATE_FORMAT"

    def test_hourly_date_out_of_range(self):
        resp = client.get("/api/v1/weather/hourly?latitude=17.385&longitude=78.4867&date=2028-01-01")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "DATE_OUT_OF_RANGE"
