"""
app/services/alert_service.py

CRUD and evaluation operations for meteorological alerts.
Backed by Supabase (Postgres) with graceful in-memory fallback if Supabase
is not configured or unavailable during local development / testing.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.database import get_supabase
from app.schemas.alert import (
    AlertBatchEvaluationResponse,
    AlertCondition,
    AlertEvaluationItem,
    AlertRequest,
    AlertResponse,
)
from app.services.weather_service import get_current_weather

logger = logging.getLogger(__name__)

# In-memory fallback store for offline dev or test runs without Supabase
_IN_MEMORY_ALERTS: Dict[str, AlertResponse] = {}


def _generate_alert_id() -> str:
    """Generate a clean identifier for an alert (e.g. alert_a1b2c3d4)."""
    return f"alert_{uuid.uuid4().hex[:8]}"


async def create_alert(alert_in: AlertRequest) -> AlertResponse:
    """
    Create and persist a new alert.
    Writes to Supabase table 'alerts' if available, otherwise in-memory.
    """
    alert_id = _generate_alert_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    alert_obj = AlertResponse(
        id=alert_id,
        latitude=alert_in.latitude,
        longitude=alert_in.longitude,
        condition=alert_in.condition.value,
        threshold=alert_in.threshold,
        active=True,
        location_name=alert_in.location_name,
        triggered=False,
        current_value=None,
        evaluated_at=None,
        last_triggered_at=None,
        created_at=now_iso,
    )

    sb = get_supabase()
    if sb is not None:
        try:
            row = {
                "id": alert_obj.id,
                "latitude": alert_obj.latitude,
                "longitude": alert_obj.longitude,
                "condition": alert_obj.condition,
                "threshold": alert_obj.threshold,
                "active": alert_obj.active,
                "location_name": alert_obj.location_name,
                "triggered": alert_obj.triggered,
                "current_value": alert_obj.current_value,
                "evaluated_at": alert_obj.evaluated_at,
                "last_triggered_at": alert_obj.last_triggered_at,
                "created_at": alert_obj.created_at,
            }
            res = sb.table("alerts").insert(row).execute()
            if res.data:
                logger.info("Alert %s saved to Supabase", alert_id)
                _IN_MEMORY_ALERTS[alert_id] = alert_obj
                return alert_obj
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase insert failed (%s) — saving to in-memory fallback store",
                exc,
            )

    # In-memory fallback
    _IN_MEMORY_ALERTS[alert_id] = alert_obj
    logger.info("Alert %s saved to in-memory store", alert_id)
    return alert_obj


async def list_alerts() -> List[AlertResponse]:
    """Retrieve all configured alerts."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = sb.table("alerts").select("*").order("created_at", desc=True).execute()
            if res.data is not None:
                supabase_alerts = [
                    AlertResponse(
                        id=row["id"],
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        condition=row["condition"],
                        threshold=float(row["threshold"]),
                        active=bool(row.get("active", True)),
                        location_name=row.get("location_name"),
                        triggered=bool(row.get("triggered", False)),
                        current_value=(
                            float(row["current_value"])
                            if row.get("current_value") is not None
                            else None
                        ),
                        evaluated_at=row.get("evaluated_at"),
                        last_triggered_at=row.get("last_triggered_at"),
                        created_at=row.get("created_at"),
                    )
                    for row in res.data
                ]
                # Merge with any fallback alerts that couldn't be inserted into Supabase
                merged = {a.id: a for a in _IN_MEMORY_ALERTS.values()}
                for a in supabase_alerts:
                    merged[a.id] = a
                return list(merged.values())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase list failed (%s) — returning in-memory fallback alerts",
                exc,
            )

    return list(_IN_MEMORY_ALERTS.values())


async def get_alert(alert_id: str) -> Optional[AlertResponse]:
    """Retrieve a single alert by ID."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = sb.table("alerts").select("*").eq("id", alert_id).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                return AlertResponse(
                    id=row["id"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    condition=row["condition"],
                    threshold=float(row["threshold"]),
                    active=bool(row.get("active", True)),
                    location_name=row.get("location_name"),
                    triggered=bool(row.get("triggered", False)),
                    current_value=(
                        float(row["current_value"])
                        if row.get("current_value") is not None
                        else None
                    ),
                    evaluated_at=row.get("evaluated_at"),
                    last_triggered_at=row.get("last_triggered_at"),
                    created_at=row.get("created_at"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase get failed (%s) — querying in-memory store", exc)

    return _IN_MEMORY_ALERTS.get(alert_id)


async def delete_alert(alert_id: str) -> bool:
    """
    Delete an alert by ID.
    Returns True if found and deleted, False otherwise.
    """
    found = False

    existing = await get_alert(alert_id)
    if existing is None and alert_id not in _IN_MEMORY_ALERTS:
        return False

    sb = get_supabase()
    if sb is not None:
        try:
            res = sb.table("alerts").delete().eq("id", alert_id).execute()
            if res.data and len(res.data) > 0:
                found = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase delete failed (%s) — falling back to in-memory", exc)

    if alert_id in _IN_MEMORY_ALERTS:
        del _IN_MEMORY_ALERTS[alert_id]
        found = True

    return found


async def evaluate_alert(alert: AlertResponse) -> AlertEvaluationItem:
    """
    Evaluate an individual alert against real-time Open-Meteo weather data.
    Updates alert's triggered status, current_value, evaluated_at, and last_triggered_at.
    Prevents repeated triggering events if condition remains above threshold.
    Re-triggers when condition drops below threshold and later crosses again.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        current_data = await get_current_weather(
            alert.latitude, alert.longitude, location_name=alert.location_name or "Alert Location"
        )
        cw = current_data.current

        # Extract metric value based on alert condition
        if alert.condition == AlertCondition.RAIN_PROBABILITY.value:
            observed_val = float(cw.rain_probability if cw.rain_probability is not None else 0.0)
        elif alert.condition == AlertCondition.TEMPERATURE.value:
            observed_val = float(cw.temperature)
        elif alert.condition == AlertCondition.WIND_SPEED.value:
            observed_val = float(cw.wind_speed)
        elif alert.condition == AlertCondition.PRECIPITATION.value:
            observed_val = float(cw.precipitation)
        else:
            observed_val = 0.0

        # Check threshold condition: trigger when observed_val >= threshold
        is_above_threshold = observed_val >= alert.threshold

        was_triggered = alert.triggered
        if is_above_threshold:
            alert.triggered = True
            # Transition from not-triggered -> triggered: record last_triggered_at
            if not was_triggered or alert.last_triggered_at is None:
                alert.last_triggered_at = now_iso
            # If already triggered, maintain existing last_triggered_at (no repeated trigger event)
        else:
            # Condition fell below threshold: reset triggered state
            alert.triggered = False

        alert.current_value = round(observed_val, 2)
        alert.evaluated_at = now_iso

        # Persist updated state to Supabase or in-memory
        sb = get_supabase()
        if sb is not None:
            try:
                sb.table("alerts").update(
                    {
                        "triggered": alert.triggered,
                        "current_value": alert.current_value,
                        "evaluated_at": alert.evaluated_at,
                        "last_triggered_at": alert.last_triggered_at,
                    }
                ).eq("id", alert.id).execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Supabase update for alert %s failed: %s", alert.id, exc)

        _IN_MEMORY_ALERTS[alert.id] = alert

        return AlertEvaluationItem(
            alert_id=alert.id,
            triggered=alert.triggered,
            current_value=alert.current_value,
            condition=alert.condition,
            threshold=alert.threshold,
            evaluated_at=now_iso,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Error evaluating alert %s: %s", alert.id, exc)
        return AlertEvaluationItem(
            alert_id=alert.id,
            triggered=alert.triggered,
            current_value=alert.current_value,
            condition=alert.condition,
            threshold=alert.threshold,
            evaluated_at=now_iso,
            error=f"Weather check failed: {exc}",
        )


async def evaluate_all_alerts() -> AlertBatchEvaluationResponse:
    """
    Evaluate all active alerts immediately.
    Used by POST /api/v1/alerts/evaluate and by the background scheduler.
    """
    alerts = await list_alerts()
    active_alerts = [a for a in alerts if a.active]

    results: List[AlertEvaluationItem] = []
    for a in active_alerts:
        res = await evaluate_alert(a)
        results.append(res)

    triggered_count = sum(1 for r in results if r.triggered)

    logger.info(
        "Alert evaluation cycle complete: %d evaluated, %d triggered",
        len(results),
        triggered_count,
    )

    return AlertBatchEvaluationResponse(
        evaluated=len(results),
        triggered=triggered_count,
        results=results,
    )


def clear_in_memory_alerts():
    """Utility for test suites to reset memory store."""
    _IN_MEMORY_ALERTS.clear()
