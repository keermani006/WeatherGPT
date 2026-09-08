"""
tests/test_climate.py

Unit and integration tests for /api/v1/climate endpoint backed by Open-Meteo Climate API:
  - Valid coordinates and default date parameters
  - Custom date range and model parameters
  - Validation: invalid coordinates, bad date format, reversed date range, span too large
  - Statistical summary calculations (mean temp, total precip, mean humidity, mean wind)
  - Trend calculations (temperature, precipitation)
  - Attribution notice verification
  - Upstream error handling (502 HTTP error, 503 timeout)
"""

from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_MOCK_CLIMATE_PAYLOAD = {
    "latitude": 17.385,
    "longitude": 78.4867,
    "daily_units": {
        "time": "iso8601",
        "temperature_2m_mean": "°C",
        "temperature_2m_max": "°C",
        "temperature_2m_min": "°C",
        "precipitation_sum": "mm",
        "relative_humidity_2m_mean": "%",
        "wind_speed_10m_mean": "km/h",
    },
    "daily": {
        "time": [
            "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04",
            "2025-01-05", "2025-01-06", "2025-01-07", "2025-01-08",
        ],
        "temperature_2m_mean": [24.0, 24.2, 24.5, 24.8, 25.5, 26.0, 26.5, 27.0],
        "temperature_2m_max": [28.0, 28.5, 29.0, 29.5, 30.0, 30.5, 31.0, 31.5],
        "temperature_2m_min": [20.0, 20.2, 20.5, 20.8, 21.0, 21.5, 22.0, 22.5],
        "precipitation_sum": [1.0, 0.0, 0.5, 2.0, 0.0, 0.0, 0.0, 0.0],
        "relative_humidity_2m_mean": [65.0, 64.0, 63.0, 62.0, 60.0, 58.0, 57.0, 55.0],
        "wind_speed_10m_mean": [12.0, 11.5, 13.0, 14.0, 12.5, 13.5, 14.5, 15.0],
    },
}


class TestClimate:
    def test_valid_climate_request_with_defaults(self):
        mock_resp = httpx.Response(
            status_code=200,
            json=_MOCK_CLIMATE_PAYLOAD,
            request=httpx.Request("GET", "https://climate-api.open-meteo.com/v1/climate"),
        )
        with (
            patch("app.api.routes.climate.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)),
        ):
            resp = client.get("/api/v1/climate?latitude=17.385&longitude=78.4867")

        assert resp.status_code == 200
        data = resp.json()
        assert data["location"]["latitude"] == 17.385
        assert data["location"]["longitude"] == 78.4867
        assert data["location"]["name"] == "Hyderabad"
        assert len(data["daily"]) == 8
        assert data["model"] == "CMCC_CM2_VHR4"
        assert data["data_source"]["provider"] == "Open-Meteo"
        assert data["data_source"]["dataset"] == "CMIP6"
        assert "Open-Meteo / CMIP6" in data["data_source"]["attribution"]

    def test_summary_and_trend_calculations(self):
        mock_resp = httpx.Response(
            status_code=200,
            json=_MOCK_CLIMATE_PAYLOAD,
            request=httpx.Request("GET", "https://climate-api.open-meteo.com/v1/climate"),
        )
        with (
            patch("app.api.routes.climate.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)),
        ):
            resp = client.get("/api/v1/climate?latitude=17.385&longitude=78.4867")

        assert resp.status_code == 200
        summary = resp.json()["summary"]
        # temperatures: [24.0, 24.2, 24.5, 24.8, 25.5, 26.0, 26.5, 27.0] -> mean = 25.3
        assert summary["average_temperature"] == 25.3
        # precipitation: sum of [1.0, 0.0, 0.5, 2.0, 0.0, 0.0, 0.0, 0.0] -> 3.5
        assert summary["total_precipitation"] == 3.5
        # humidity: mean of [65, 64, 63, 62, 60, 58, 57, 55] -> 60.5
        assert summary["average_humidity"] == 60.5
        # wind: mean of [12, 11.5, 13, 14, 12.5, 13.5, 14.5, 15] -> 13.25 -> 13.2
        assert summary["average_wind_speed"] == 13.2

        # Trend: first half temp mean = 24.375, second half = 26.25 -> diff = +1.875 > 0.5 -> "increasing"
        trend = resp.json()["trend"]
        assert trend["temperature"] == "increasing"

    def test_custom_date_range_and_model(self):
        mock_resp = httpx.Response(
            status_code=200,
            json=_MOCK_CLIMATE_PAYLOAD,
            request=httpx.Request("GET", "https://climate-api.open-meteo.com/v1/climate"),
        )
        with (
            patch("app.api.routes.climate.reverse_geocode", AsyncMock(return_value="Chennai")),
            patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)),
        ):
            resp = client.get(
                "/api/v1/climate?latitude=13.0827&longitude=80.2707"
                "&start_date=2025-06-01&end_date=2025-06-08&model=EC_Earth3P_HR"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["period"]["start"] == "2025-06-01"
        assert data["period"]["end"] == "2025-06-08"
        assert data["model"] == "EC_Earth3P_HR"

    def test_missing_coordinates(self):
        resp = client.get("/api/v1/climate")
        assert resp.status_code == 422

    def test_invalid_coordinates(self):
        resp = client.get("/api/v1/climate?latitude=999.0&longitude=80.0")
        assert resp.status_code == 422

    def test_invalid_date_format(self):
        resp = client.get("/api/v1/climate?latitude=17.385&longitude=78.4867&start_date=01-01-2025")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_DATE_FORMAT"

    def test_start_date_after_end_date(self):
        resp = client.get(
            "/api/v1/climate?latitude=17.385&longitude=78.4867"
            "&start_date=2025-02-01&end_date=2025-01-01"
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_DATE_RANGE"

    def test_date_range_too_large(self):
        resp = client.get(
            "/api/v1/climate?latitude=17.385&longitude=78.4867"
            "&start_date=2000-01-01&end_date=2025-01-01"
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "DATE_RANGE_TOO_LARGE"

    def test_climate_upstream_timeout(self):
        with (
            patch("app.api.routes.climate.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch(
                "httpx.AsyncClient.get",
                AsyncMock(side_effect=httpx.TimeoutException("Open-Meteo climate timed out")),
            ),
        ):
            resp = client.get("/api/v1/climate?latitude=17.385&longitude=78.4867")

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "CLIMATE_SERVICE_TIMEOUT"

    def test_climate_upstream_http_error(self):
        mock_response = httpx.Response(status_code=500, request=httpx.Request("GET", "https://climate-api.open-meteo.com"))
        with (
            patch("app.api.routes.climate.reverse_geocode", AsyncMock(return_value="Hyderabad")),
            patch(
                "httpx.AsyncClient.get",
                AsyncMock(side_effect=httpx.HTTPStatusError("Internal Server Error", request=mock_response.request, response=mock_response)),
            ),
        ):
            resp = client.get("/api/v1/climate?latitude=17.385&longitude=78.4867")

        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "CLIMATE_SERVICE_ERROR"
