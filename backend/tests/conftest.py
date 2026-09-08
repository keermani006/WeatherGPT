"""
tests/conftest.py

Shared pytest fixtures and helpers for the WeatherGPT test suite.

Provides:
  - mock_jwt_user: patches get_current_user to return a deterministic test user
  - auth_headers: Authorization header with a fake bearer token (intercepted by mock)
  - override_settings: allows overriding specific settings per-test
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.auth import AuthUser, get_current_user
from app.core.cache import weather_cache, location_cache, climate_cache
from app.main import app

# ── Shared test constants ──────────────────────────────────────────────────────

TEST_USER_ID = "test-user-uuid-12345678"
TEST_USER_2_ID = "other-user-uuid-87654321"
FAKE_AUTH_HEADER = {"Authorization": "Bearer fake-test-token-abc123"}


# ── Fixtures ───────────────────────────────────────────────────────────────────

from app.core.resilience import cb_open_meteo, cb_climate, cb_nominatim, cb_groq

@pytest.fixture(autouse=True)
def clear_all_caches():
    """Clear in-memory caches and reset circuit breakers between test runs."""
    weather_cache.clear()
    location_cache.clear()
    climate_cache.clear()
    cb_open_meteo.reset()
    cb_climate.reset()
    cb_nominatim.reset()
    cb_groq.reset()
    yield
    weather_cache.clear()
    location_cache.clear()
    climate_cache.clear()
    cb_open_meteo.reset()
    cb_climate.reset()
    cb_nominatim.reset()
    cb_groq.reset()


@pytest.fixture
def client():
    """Return a TestClient with auth mocked out."""
    return TestClient(app)


@pytest.fixture
def auth_user() -> AuthUser:
    """Return a deterministic test AuthUser."""
    return AuthUser(user_id=TEST_USER_ID, email="test@example.com", role="authenticated")


@pytest.fixture
def mock_auth(auth_user):
    """
    Override get_current_user to always return the test user.
    Use this on tests that call protected endpoints.
    """
    app.dependency_overrides[get_current_user] = lambda: auth_user
    yield auth_user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def other_user() -> AuthUser:
    return AuthUser(user_id=TEST_USER_2_ID, email="other@example.com", role="authenticated")


def make_weather_response(
    temperature=25.0, feels_like=26.0, humidity=50,
    wind_speed=10.0, precipitation=0.0, rain_probability=20,
    condition="Partly cloudy", location_name="Test City",
    lat=17.385, lon=78.487,
):
    """Helper to build a mock CurrentWeatherResponse."""
    from app.schemas.weather import CurrentConditions, CurrentWeatherResponse, LocationInfo
    return CurrentWeatherResponse(
        location=LocationInfo(name=location_name, latitude=lat, longitude=lon),
        current=CurrentConditions(
            temperature=temperature, feels_like=feels_like, humidity=humidity,
            wind_speed=wind_speed, precipitation=precipitation,
            rain_probability=rain_probability, condition=condition,
        ),
        updated_at="2026-09-08T15:30",
    )
