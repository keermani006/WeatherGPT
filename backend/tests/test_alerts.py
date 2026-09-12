"""
tests/test_alerts.py

Tests for /api/v1/alerts endpoints and alert evaluation.
Phase 2: all CRUD endpoints require authentication (mocked via conftest).
"""

from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import AuthUser
from app.services.alert_service import clear_in_memory_alerts, evaluate_all_alerts
from tests.conftest import TEST_USER_ID, TEST_USER_2_ID, make_weather_response

client = TestClient(app)

TEST_USER = AuthUser(user_id=TEST_USER_ID)
OTHER_USER = AuthUser(user_id=TEST_USER_2_ID)


@pytest.fixture(autouse=True)
def reset_alerts():
    clear_in_memory_alerts()
    with patch("app.services.alert_service.get_supabase", return_value=None):
        yield
    clear_in_memory_alerts()


from app.core.auth import get_current_user


def _authed_post(payload, user=TEST_USER):
    """POST /api/v1/alerts with mocked authentication."""
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return client.post("/api/v1/alerts", json=payload,
                           headers={"Authorization": "Bearer fake-token"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _authed_get(user=TEST_USER):
    """GET /api/v1/alerts with mocked authentication."""
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return client.get("/api/v1/alerts", headers={"Authorization": "Bearer fake-token"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _authed_delete(alert_id, user=TEST_USER):
    """DELETE /api/v1/alerts/{id} with mocked authentication."""
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return client.delete(f"/api/v1/alerts/{alert_id}",
                             headers={"Authorization": "Bearer fake-token"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)


class TestAlerts:
    def test_create_alert_valid(self):
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyderabad")):
            resp = _authed_post({
                "latitude": 17.385, "longitude": 78.4867,
                "condition": "rain_probability", "threshold": 60.0,
            })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("alert_")
        assert data["condition"] == "rain_probability"
        assert data["threshold"] == 60.0
        assert data["active"] is True
        assert data["triggered"] is False
        assert data["location_name"] == "Hyderabad"

    def test_create_alert_with_explicit_location_name(self):
        resp = _authed_post({
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "temperature", "threshold": 35.0,
            "location_name": "Hyderabad City",
        })
        assert resp.status_code == 201
        assert resp.json()["location_name"] == "Hyderabad City"

    def test_create_alert_invalid_condition(self):
        resp = _authed_post({
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "tornado_warning", "threshold": 10.0,
        })
        assert resp.status_code == 422

    def test_create_alert_invalid_threshold_rain(self):
        resp = _authed_post({
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "rain_probability", "threshold": 150.0,
        })
        assert resp.status_code == 422

    def test_create_alert_invalid_threshold_temperature(self):
        resp = _authed_post({
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "temperature", "threshold": 200.0,
        })
        assert resp.status_code == 422

    def test_create_alert_wind_speed_negative(self):
        resp = _authed_post({
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "wind_speed", "threshold": -5.0,
        })
        assert resp.status_code == 422

    def test_create_alert_invalid_coords(self):
        resp = _authed_post({
            "latitude": 999, "longitude": 78.4867,
            "condition": "temperature", "threshold": 30.0,
        })
        assert resp.status_code == 422

    def test_create_alert_requires_auth(self):
        """POST without auth header must return 401 or 503 (not configured)."""
        resp = client.post("/api/v1/alerts", json={
            "latitude": 17.385, "longitude": 78.4867,
            "condition": "temperature", "threshold": 35.0,
        })
        assert resp.status_code in (401, 503)

    def test_list_alerts_empty(self):
        resp = _authed_get()
        assert resp.status_code == 200
        assert resp.json()["alerts"] == []

    def test_list_alerts_returns_own_alerts(self):
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyd")):
            _authed_post({"latitude": 17.385, "longitude": 78.4867,
                          "condition": "temperature", "threshold": 35.0})
        resp = _authed_get()
        assert resp.status_code == 200
        alerts = resp.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["condition"] == "temperature"

    def test_list_alerts_requires_auth(self):
        resp = client.get("/api/v1/alerts")
        assert resp.status_code in (401, 503)

    def test_user_isolation(self):
        """User A's alerts must not be visible to User B."""
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyd")):
            _authed_post({"latitude": 17.385, "longitude": 78.4867,
                          "condition": "temperature", "threshold": 35.0},
                         user=TEST_USER)

        # User B should see empty list
        resp = _authed_get(user=OTHER_USER)
        assert resp.status_code == 200
        assert resp.json()["alerts"] == []

    def test_delete_alert_success(self):
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyd")):
            create_resp = _authed_post({"latitude": 17.385, "longitude": 78.4867,
                                        "condition": "wind_speed", "threshold": 20.0})
        alert_id = create_resp.json()["id"]
        del_resp = _authed_delete(alert_id)
        assert del_resp.status_code == 200
        assert del_resp.json()["id"] == alert_id

    def test_delete_alert_not_found(self):
        resp = _authed_delete("alert_nonexistent999")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "ALERT_NOT_FOUND"

    def test_delete_alert_ownership_check(self):
        """User B cannot delete User A's alert."""
        with patch("app.api.routes.alerts.reverse_geocode", AsyncMock(return_value="Hyd")):
            create_resp = _authed_post({"latitude": 17.385, "longitude": 78.4867,
                                        "condition": "temperature", "threshold": 35.0},
                                       user=TEST_USER)
        alert_id = create_resp.json()["id"]
        # Manually set user_id on the in-memory alert so ownership check fires
        from app.services.alert_service import _IN_MEMORY_ALERTS
        _IN_MEMORY_ALERTS[alert_id].user_id = TEST_USER_ID

        del_resp = _authed_delete(alert_id, user=OTHER_USER)
        assert del_resp.status_code == 403

    def test_delete_requires_auth(self):
        resp = client.delete("/api/v1/alerts/alert_fake123")
        assert resp.status_code in (401, 503)

    def test_evaluate_endpoint_public(self):
        """POST /evaluate is public (no auth required)."""
        resp = client.post("/api/v1/alerts/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert "evaluated" in data
        assert "triggered" in data

    def test_evaluate_no_alerts(self):
        resp = client.post("/api/v1/alerts/evaluate")
        data = resp.json()
        assert data["evaluated"] == 0
        assert data["triggered"] == 0
        assert data["results"] == []


class TestAlertEvaluation:
    @pytest.fixture
    def alert_below_threshold(self):
        from app.schemas.alert import AlertResponse
        return AlertResponse(
            id="alert_test001", user_id=TEST_USER_ID,
            latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0,
            active=True, location_name="Hyderabad",
            triggered=False, current_value=None,
        )

    @pytest.fixture
    def alert_at_threshold(self):
        from app.schemas.alert import AlertResponse
        return AlertResponse(
            id="alert_test002", user_id=TEST_USER_ID,
            latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0,
            active=True, location_name="Hyderabad",
            triggered=False, current_value=None,
        )

    def test_temperature_alert_triggered(self):
        """Alert triggers when observed value >= threshold."""
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_t1", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 36.0)
            )
        assert result.triggered is True
        assert result.current_value == 36.0

    def test_temperature_alert_not_triggered(self):
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_t2", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 30.0)
            )
        assert result.triggered is False

    def test_rain_probability_alert(self):
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_r1", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="rain_probability", threshold=70.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 80.0)
            )
        assert result.triggered is True

    def test_wind_speed_alert(self):
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_w1", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="wind_speed", threshold=15.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 20.0)
            )
        assert result.triggered is True

    def test_precipitation_alert(self):
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_p1", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="precipitation", threshold=10.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 5.0)
            )
        assert result.triggered is False

    def test_trigger_transition_sets_last_triggered_at(self):
        """false → true sets last_triggered_at."""
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_trans1", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True,
            triggered=False, last_triggered_at=None,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 40.0)
            )
        assert result.triggered is True
        assert alert.last_triggered_at is not None

    def test_already_triggered_does_not_update_last_triggered_at(self):
        """true → true keeps existing last_triggered_at (anti-spam)."""
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        original_ts = "2026-09-08T10:00:00+00:00"
        alert = AlertResponse(
            id="alert_trans2", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True,
            triggered=True, last_triggered_at=original_ts,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            asyncio.run(
                evaluate_alert_with_weather(alert, 40.0)
            )
        assert alert.last_triggered_at == original_ts  # unchanged

    def test_reset_when_drops_below_threshold(self):
        """true → false resets triggered when condition clears."""
        from app.services.alert_service import evaluate_alert_with_weather
        import asyncio
        from app.schemas.alert import AlertResponse
        alert = AlertResponse(
            id="alert_trans3", user_id=TEST_USER_ID, latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True,
            triggered=True, last_triggered_at="2026-09-08T10:00:00+00:00",
        )
        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(
                evaluate_alert_with_weather(alert, 28.0)
            )
        assert result.triggered is False

    def test_evaluate_all_groups_by_coordinate(self):
        """Multiple alerts at the same location → single weather API call."""
        from app.services.alert_service import _IN_MEMORY_ALERTS
        from app.schemas.alert import AlertResponse

        # Add 3 alerts at the same coordinates
        for i in range(3):
            _IN_MEMORY_ALERTS[f"alert_g{i}"] = AlertResponse(
                id=f"alert_g{i}", user_id=TEST_USER_ID,
                latitude=17.385, longitude=78.4867,
                condition="temperature", threshold=35.0, active=True, triggered=False,
            )

        mock_weather = make_weather_response(temperature=25.0)
        import asyncio
        with patch("app.services.alert_service.get_supabase", return_value=None), \
             patch("app.services.alert_service.get_current_weather",
                   AsyncMock(return_value=mock_weather)) as mock_call:
            result = asyncio.run(evaluate_all_alerts())

        assert result.evaluated == 3
        # Only 1 weather call for 3 alerts at same location
        assert mock_call.call_count == 1

    def test_evaluate_all_handles_weather_failure_gracefully(self):
        """Weather fetch failure → error in result, not crash."""
        from app.services.alert_service import _IN_MEMORY_ALERTS
        from app.schemas.alert import AlertResponse
        import asyncio

        _IN_MEMORY_ALERTS["alert_fail1"] = AlertResponse(
            id="alert_fail1", user_id=TEST_USER_ID,
            latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True, triggered=False,
        )
        with patch("app.services.alert_service.get_supabase", return_value=None), \
             patch("app.services.alert_service.get_current_weather",
                   AsyncMock(side_effect=Exception("Network error"))):
            result = asyncio.run(evaluate_all_alerts())

        assert result.evaluated == 1
        assert result.results[0].error is not None

    def test_evaluate_scheduler_overlap_protection(self):
        """Second call while first is running returns 0 evaluated."""
        import asyncio
        from app.services.alert_service import _IN_MEMORY_ALERTS, _evaluation_lock
        from app.schemas.alert import AlertResponse

        _IN_MEMORY_ALERTS["alert_overlap"] = AlertResponse(
            id="alert_overlap", user_id=TEST_USER_ID,
            latitude=17.385, longitude=78.4867,
            condition="temperature", threshold=35.0, active=True, triggered=False,
        )

        async def _test():
            async with _evaluation_lock:
                # While lock is held, evaluate_all_alerts should skip
                result = await evaluate_all_alerts()
                return result

        with patch("app.services.alert_service.get_supabase", return_value=None):
            result = asyncio.run(_test())

        assert result.evaluated == 0
