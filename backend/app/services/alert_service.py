"""
app/services/alert_service.py

CRUD and evaluation operations for meteorological alerts.

Key improvements in Phase 2:
  - user_id is mandatory for all CRUD operations (enforced at service layer).
  - evaluate_all_alerts groups alerts by coordinate bucket to minimise
    Open-Meteo API calls (N unique locations → N requests instead of N alerts).
  - _evaluation_lock prevents overlapping scheduler cycles.
  - Backed by Supabase (service-role key bypasses RLS); falls back to
    in-memory store for offline dev / testing.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
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

# Prevent overlapping evaluation cycles from the background scheduler
_evaluation_lock = asyncio.Lock()


def _generate_alert_id() -> str:
    return f"alert_{uuid.uuid4().hex[:8]}"


def _coord_bucket(lat: float, lon: float) -> tuple:
    """Round to 2 decimal places to group nearby alerts into one weather call."""
    return (round(lat, 2), round(lon, 2))


def _row_to_alert(row: dict) -> AlertResponse:
    return AlertResponse(
        id=row["id"],
        user_id=row.get("user_id"),
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


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_alert(alert_in: AlertRequest, user_id: str) -> AlertResponse:
    """
    Create and persist a new alert owned by user_id.
    Writes to Supabase if available, otherwise in-memory.
    """
    alert_id = _generate_alert_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    alert_obj = AlertResponse(
        id=alert_id,
        user_id=user_id,
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
                "user_id": user_id,
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
                logger.info("Alert %s saved to Supabase (user=%s)", alert_id, user_id)
                _IN_MEMORY_ALERTS[alert_id] = alert_obj
                return alert_obj
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase insert failed (%s) — saving to in-memory fallback store", exc
            )

    _IN_MEMORY_ALERTS[alert_id] = alert_obj
    logger.info("Alert %s saved to in-memory store (user=%s)", alert_id, user_id)
    return alert_obj


async def list_alerts(user_id: str) -> List[AlertResponse]:
    """Retrieve alerts belonging to user_id."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("alerts")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )
            if res.data is not None:
                supabase_alerts = [_row_to_alert(row) for row in res.data]
                # Merge with in-memory fallback alerts owned by this user
                merged = {
                    a.id: a
                    for a in _IN_MEMORY_ALERTS.values()
                    if a.user_id == user_id
                }
                for a in supabase_alerts:
                    merged[a.id] = a
                return list(merged.values())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Supabase list failed (%s) — returning in-memory fallback", exc
            )

    return [a for a in _IN_MEMORY_ALERTS.values() if a.user_id == user_id]


async def get_alert(alert_id: str) -> Optional[AlertResponse]:
    """Retrieve a single alert by ID (any user — used by scheduler)."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = sb.table("alerts").select("*").eq("id", alert_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_alert(res.data[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase get failed (%s) — querying in-memory store", exc)

    return _IN_MEMORY_ALERTS.get(alert_id)


async def delete_alert(alert_id: str, user_id: str) -> bool:
    """
    Delete an alert by ID, enforcing ownership at the service layer.
    Returns True if found and deleted, False if not found, raises PermissionError if not owned.
    """
    existing = await get_alert(alert_id)
    if existing is None and alert_id not in _IN_MEMORY_ALERTS:
        return False

    # Backend ownership check (independent of RLS)
    if existing and existing.user_id and existing.user_id != user_id:
        logger.warning(
            "Ownership violation: user %s attempted to delete alert %s owned by %s",
            user_id, alert_id, existing.user_id,
        )
        raise PermissionError(f"Alert '{alert_id}' does not belong to the authenticated user.")

    found = False
    sb = get_supabase()
    if sb is not None:
        try:
            res = (
                sb.table("alerts")
                .delete()
                .eq("id", alert_id)
                .eq("user_id", user_id)
                .execute()
            )
            if res.data and len(res.data) > 0:
                found = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase delete failed (%s) — falling back to in-memory", exc)

    if alert_id in _IN_MEMORY_ALERTS:
        mem_alert = _IN_MEMORY_ALERTS[alert_id]
        if mem_alert.user_id is None or mem_alert.user_id == user_id:
            del _IN_MEMORY_ALERTS[alert_id]
            found = True

    return found


# ── Evaluation ────────────────────────────────────────────────────────────────

async def evaluate_alert_with_weather(
    alert: AlertResponse, weather_value: float
) -> AlertEvaluationItem:
    """
    Evaluate a single alert against an already-fetched weather value.
    Updates persistence (Supabase or in-memory).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    is_above = weather_value >= alert.threshold
    was_triggered = alert.triggered

    if is_above:
        alert.triggered = True
        if not was_triggered or alert.last_triggered_at is None:
            alert.last_triggered_at = now_iso  # New trigger event
    else:
        alert.triggered = False  # Reset when condition clears

    alert.current_value = round(weather_value, 2)
    alert.evaluated_at = now_iso

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


def _extract_condition_value(alert: AlertResponse, weather) -> float:
    """Extract the relevant metric from current weather conditions."""
    cw = weather.current
    if alert.condition == AlertCondition.RAIN_PROBABILITY.value:
        return float(cw.rain_probability if cw.rain_probability is not None else 0.0)
    if alert.condition == AlertCondition.TEMPERATURE.value:
        return float(cw.temperature)
    if alert.condition == AlertCondition.WIND_SPEED.value:
        return float(cw.wind_speed)
    if alert.condition == AlertCondition.PRECIPITATION.value:
        return float(cw.precipitation)
    return 0.0


async def _list_all_alerts_for_scheduler() -> List[AlertResponse]:
    """Retrieve all alerts (any user) for the background scheduler."""
    sb = get_supabase()
    if sb is not None:
        try:
            res = sb.table("alerts").select("*").order("created_at", desc=True).execute()
            if res.data is not None:
                supabase_alerts = [_row_to_alert(row) for row in res.data]
                merged = {a.id: a for a in _IN_MEMORY_ALERTS.values()}
                for a in supabase_alerts:
                    merged[a.id] = a
                return list(merged.values())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase list-all failed (%s) — using in-memory", exc)
    return list(_IN_MEMORY_ALERTS.values())


async def evaluate_all_alerts() -> AlertBatchEvaluationResponse:
    """
    Evaluate all active alerts.

    Optimisation: alerts are grouped by coordinate bucket so that N alerts
    at the same location generate only 1 Open-Meteo request.

    Overlap protection: skips evaluation if a previous cycle is still running
    (prevents pile-up under slow Open-Meteo responses).
    """
    if _evaluation_lock.locked():
        logger.warning("Alert evaluation skipped — previous cycle still running")
        return AlertBatchEvaluationResponse(evaluated=0, triggered=0, results=[])

    async with _evaluation_lock:
        alerts = await _list_all_alerts_for_scheduler()
        active_alerts = [a for a in alerts if a.active]

        if not active_alerts:
            logger.info("Alert evaluation: no active alerts to evaluate")
            return AlertBatchEvaluationResponse(evaluated=0, triggered=0, results=[])

        # Group alerts by coordinate bucket to minimise upstream API calls
        buckets: dict = defaultdict(list)
        for alert in active_alerts:
            bucket = _coord_bucket(alert.latitude, alert.longitude)
            buckets[bucket].append(alert)

        logger.info(
            "Alert evaluation: %d alerts across %d location bucket(s)",
            len(active_alerts), len(buckets),
        )

        results: List[AlertEvaluationItem] = []
        for (lat, lon), bucket_alerts in buckets.items():
            location_name = bucket_alerts[0].location_name or "Alert Location"
            try:
                weather = await get_current_weather(lat, lon, location_name)
                for alert in bucket_alerts:
                    try:
                        value = _extract_condition_value(alert, weather)
                        result = await evaluate_alert_with_weather(alert, value)
                        results.append(result)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Error evaluating alert %s: %s", alert.id, exc)
                        results.append(AlertEvaluationItem(
                            alert_id=alert.id,
                            triggered=alert.triggered,
                            current_value=alert.current_value,
                            condition=alert.condition,
                            threshold=alert.threshold,
                            evaluated_at=datetime.now(timezone.utc).isoformat(),
                            error=f"Evaluation error: {exc}",
                        ))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Weather fetch failed for bucket (%.2f, %.2f): %s", lat, lon, exc
                )
                for alert in bucket_alerts:
                    results.append(AlertEvaluationItem(
                        alert_id=alert.id,
                        triggered=alert.triggered,
                        current_value=alert.current_value,
                        condition=alert.condition,
                        threshold=alert.threshold,
                        evaluated_at=datetime.now(timezone.utc).isoformat(),
                        error=f"Weather fetch failed: {exc}",
                    ))

        triggered_count = sum(1 for r in results if r.triggered)
        logger.info(
            "Alert evaluation complete: %d evaluated, %d triggered, %d location buckets",
            len(results), triggered_count, len(buckets),
        )
        return AlertBatchEvaluationResponse(
            evaluated=len(results),
            triggered=triggered_count,
            results=results,
        )


def clear_in_memory_alerts():
    """Utility for test suites to reset memory store."""
    _IN_MEMORY_ALERTS.clear()
