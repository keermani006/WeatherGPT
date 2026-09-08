"""
tests/test_alerts.py

Unit and integration tests for /api/v1/alerts endpoints and alert evaluation:
  - POST   /api/v1/alerts
  - GET    /api/v1/alerts
  - DELETE /api/v1/alerts/{alert_id}
  - POST   /api/v1/alerts/evaluate
  - Weather evaluation thresholds (rain, temp, wind, precip)
  - Trigger state transitions and re-triggering
  - Upstream weather failure handling during evaluation
"""

from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.weather import CurrentConditions, CurrentWeatherResponse, LocationInfo
from app.services.alert_service import clear_in_memory_alerts, evaluate_all_alerts

client = TestClient(app)


def _make_mock_weather(
    temperature: float = 25.0,
    feels_like: float = 26.0,
    humidity: int = 50,
    wind_speed: float = 10.0,
    precipitation: float = 0.0,
    rain_probability: int = 20,
    condition: str = "Partly cloudy",
) -> CurrentWeatherResponse:
    return CurrentWeatherResponse(
        location=LocationInfo(name="Test City", latitude=17.385, longitude=78.4867),
        current=CurrentConditions(
            temperature=temperature,
            feels_like=feels_like,
            humidity=humidity,
            wind_speed=wind_speed,
            precipitation=precipitation,
            rain_probability=rain_probability,
            condition=condition,
        ),
        updated_at="2026-09-08T15:30",
    )


@pytest.fixture(autouse=True)
def reset_alerts():
    clear_in_memory_alerts()
    # Force in-memory fallback during unit tests
    with patch("app.services.alert_service.get_supabase", return_value=None):
        yield
    clear_in_memory_alerts()


class TestAlerts:
    def test_create_alert_valid(self):
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyderabad")):
            payload = {
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 60.0,
            }
            resp = client.post("/api/v1/alerts", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("alert_")
        assert data["condition"] == "rain_probability"
        assert data["threshold"] == 60.0
        assert data["active"] is True
        assert data["triggered"] is False
        assert data["location_name"] == "Hyderabad"

    def test_create_alert_with_explicit_location_name(self):
        payload = {
            "latitude": 17.385,
            "longitude": 78.4867,
            "condition": "temperature",
            "threshold": 35.0,
            "location_name": "Hyderabad City",
        }
        resp = client.post("/api/v1/alerts", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["location_name"] == "Hyderabad City"
        assert data["condition"] == "temperature"
        assert data["threshold"] == 35.0

    def test_create_alert_invalid_condition(self):
        payload = {
            "latitude": 17.385,
            "longitude": 78.4867,
            "condition": "tornado_warning",
            "threshold": 10.0,
        }
        resp = client.post("/api/v1/alerts", json=payload)
        assert resp.status_code == 422

    def test_create_alert_invalid_threshold_rain_prob(self):
        payload = {
            "latitude": 17.385,
            "longitude": 78.4867,
            "condition": "rain_probability",
            "threshold": 150.0,  # Must be 0-100
        }
        resp = client.post("/api/v1/alerts", json=payload)
        assert resp.status_code == 422

    def test_list_alerts(self):
        # Create two alerts
        client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 50.0,
                "location_name": "City A",
            },
        )
        client.post(
            "/api/v1/alerts",
            json={
                "latitude": 13.0827,
                "longitude": 80.2707,
                "condition": "wind_speed",
                "threshold": 25.0,
                "location_name": "City B",
            },
        )

        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 2

    def test_delete_existing_alert(self):
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "temperature",
                "threshold": 40.0,
                "location_name": "Test Place",
            },
        )
        alert_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/alerts/{alert_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["id"] == alert_id

        # Verify deletion from list
        list_resp = client.get("/api/v1/alerts")
        ids = [a["id"] for a in list_resp.json()["alerts"]]
        assert alert_id not in ids

    def test_delete_unknown_alert_returns_404(self):
        resp = client.delete("/api/v1/alerts/alert_nonexistent_xyz")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "ALERT_NOT_FOUND"


class TestAlertEvaluation:
    """Detailed evaluation tests across all conditions and threshold crossing behaviors."""

    def test_rain_probability_below_threshold(self):
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 70.0,
            },
        )
        alert_id = create_resp.json()["id"]

        # Mock weather: 50% rain probability (< 70%)
        mock_weather = _make_mock_weather(rain_probability=50)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_weather)):
            eval_resp = client.post("/api/v1/alerts/evaluate")

        assert eval_resp.status_code == 200
        data = eval_resp.json()
        assert data["evaluated"] == 1
        assert data["triggered"] == 0
        assert data["results"][0]["alert_id"] == alert_id
        assert data["results"][0]["triggered"] is False
        assert data["results"][0]["current_value"] == 50.0

    def test_rain_probability_equal_to_threshold(self):
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 70.0,
            },
        )
        alert_id = create_resp.json()["id"]

        # Mock weather: exactly 70% (>= threshold triggers)
        mock_weather = _make_mock_weather(rain_probability=70)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_weather)):
            eval_resp = client.post("/api/v1/alerts/evaluate")

        assert eval_resp.status_code == 200
        data = eval_resp.json()
        assert data["triggered"] == 1
        assert data["results"][0]["triggered"] is True
        assert data["results"][0]["current_value"] == 70.0

    def test_rain_probability_above_threshold(self):
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 70.0,
            },
        )
        alert_id = create_resp.json()["id"]

        # Mock weather: 85% rain (> 70%)
        mock_weather = _make_mock_weather(rain_probability=85)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_weather)):
            eval_resp = client.post("/api/v1/alerts/evaluate")

        assert eval_resp.status_code == 200
        assert eval_resp.json()["triggered"] == 1
        assert eval_resp.json()["results"][0]["triggered"] is True
        assert eval_resp.json()["results"][0]["current_value"] == 85.0

    def test_temperature_threshold(self):
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 13.0827,
                "longitude": 80.2707,
                "condition": "temperature",
                "threshold": 35.0,
            },
        )
        alert_id = create_resp.json()["id"]

        # Case 1: 32°C (below threshold) -> not triggered
        mock_below = _make_mock_weather(temperature=32.0)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_below)):
            resp = client.post("/api/v1/alerts/evaluate")
        assert resp.json()["results"][0]["triggered"] is False

        # Case 2: 38.5°C (above threshold) -> triggered
        mock_above = _make_mock_weather(temperature=38.5)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_above)):
            resp = client.post("/api/v1/alerts/evaluate")
        assert resp.json()["results"][0]["triggered"] is True
        assert resp.json()["results"][0]["current_value"] == 38.5

    def test_wind_speed_threshold(self):
        client.post(
            "/api/v1/alerts",
            json={
                "latitude": 13.0827,
                "longitude": 80.2707,
                "condition": "wind_speed",
                "threshold": 30.0,
            },
        )
        # 42 km/h wind -> triggered
        mock_weather = _make_mock_weather(wind_speed=42.0)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_weather)):
            resp = client.post("/api/v1/alerts/evaluate")
        assert resp.json()["results"][0]["triggered"] is True
        assert resp.json()["results"][0]["current_value"] == 42.0

    def test_precipitation_threshold(self):
        client.post(
            "/api/v1/alerts",
            json={
                "latitude": 13.0827,
                "longitude": 80.2707,
                "condition": "precipitation",
                "threshold": 5.0,
            },
        )
        # 12.4 mm precip -> triggered
        mock_weather = _make_mock_weather(precipitation=12.4)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_weather)):
            resp = client.post("/api/v1/alerts/evaluate")
        assert resp.json()["results"][0]["triggered"] is True
        assert resp.json()["results"][0]["current_value"] == 12.4

    def test_re_trigger_after_condition_falls_and_crosses_again(self):
        """Verify state transitions: not triggered -> triggered -> not triggered -> triggered."""
        create_resp = client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "rain_probability",
                "threshold": 60.0,
            },
        )
        alert_id = create_resp.json()["id"]

        # Step 1: 80% rain -> triggers, sets last_triggered_at
        mock_w1 = _make_mock_weather(rain_probability=80)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_w1)):
            client.post("/api/v1/alerts/evaluate")

        list_resp = client.get("/api/v1/alerts")
        alert = next(a for a in list_resp.json()["alerts"] if a["id"] == alert_id)
        assert alert["triggered"] is True
        first_triggered_at = alert["last_triggered_at"]
        assert first_triggered_at is not None

        # Step 2: 85% rain -> still triggered, but last_triggered_at must NOT be updated (prevent duplicate events)
        mock_w2 = _make_mock_weather(rain_probability=85)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_w2)):
            client.post("/api/v1/alerts/evaluate")

        list_resp = client.get("/api/v1/alerts")
        alert = next(a for a in list_resp.json()["alerts"] if a["id"] == alert_id)
        assert alert["triggered"] is True
        assert alert["last_triggered_at"] == first_triggered_at

        # Step 3: 20% rain (condition drops below threshold) -> transitions to triggered=False
        mock_w3 = _make_mock_weather(rain_probability=20)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_w3)):
            client.post("/api/v1/alerts/evaluate")

        list_resp = client.get("/api/v1/alerts")
        alert = next(a for a in list_resp.json()["alerts"] if a["id"] == alert_id)
        assert alert["triggered"] is False

        # Step 4: 90% rain (crosses threshold again) -> re-triggers with new timestamp
        mock_w4 = _make_mock_weather(rain_probability=90)
        with patch("app.services.alert_service.get_current_weather", AsyncMock(return_value=mock_w4)):
            client.post("/api/v1/alerts/evaluate")

        list_resp = client.get("/api/v1/alerts")
        alert = next(a for a in list_resp.json()["alerts"] if a["id"] == alert_id)
        assert alert["triggered"] is True

    def test_weather_api_failure_during_evaluation(self):
        """Weather API error does not crash the evaluation cycle."""
        client.post(
            "/api/v1/alerts",
            json={
                "latitude": 17.385,
                "longitude": 78.4867,
                "condition": "temperature",
                "threshold": 30.0,
            },
        )
        with patch(
            "app.services.alert_service.get_current_weather",
            AsyncMock(side_effect=httpx.TimeoutException("Open-Meteo timeout")),
        ):
            resp = client.post("/api/v1/alerts/evaluate")

        assert resp.status_code == 200
        data = resp.json()
        assert data["evaluated"] == 1
        assert data["triggered"] == 0
        assert data["results"][0]["error"] is not None
        assert "timeout" in data["results"][0]["error"].lower()
