"""
app/api/routes/alerts.py

FastAPI endpoints for meteorological threshold alerts:
  - POST   /api/v1/alerts
  - GET    /api/v1/alerts
  - DELETE /api/v1/alerts/{alert_id}
"""

import logging
from fastapi import APIRouter, HTTPException, Path, status

from app.schemas.alert import (
    AlertBatchEvaluationResponse,
    AlertRequest,
    AlertResponse,
    AlertsListResponse,
    DeleteAlertResponse,
)
from app.schemas.errors import ErrorResponse
from app.services.alert_service import (
    create_alert,
    delete_alert,
    evaluate_all_alerts,
    list_alerts,
)
from app.services.location_service import reverse_geocode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.post(
    "/evaluate",
    response_model=AlertBatchEvaluationResponse,
    summary="Evaluate all active weather alerts",
    description="Immediately evaluate all active alerts against real-time Open-Meteo weather data and update triggered statuses.",
)
async def evaluate_alerts_endpoint():
    return await evaluate_all_alerts()


@router.post(
    "",
    response_model=AlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new weather alert",
    description="Register a meteorological threshold alert (rain_probability, temperature, wind_speed, precipitation).",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid threshold or condition parameters."},
        422: {"description": "Validation error."},
    },
)
async def create_alert_endpoint(alert_in: AlertRequest):
    # Auto-resolve location name if not provided
    if not alert_in.location_name:
        try:
            alert_in.location_name = await reverse_geocode(alert_in.latitude, alert_in.longitude)
        except Exception:
            alert_in.location_name = f"Location ({alert_in.latitude:.2f}, {alert_in.longitude:.2f})"

    return await create_alert(alert_in)


@router.get(
    "",
    response_model=AlertsListResponse,
    summary="List all weather alerts",
    description="Retrieve all configured weather alerts including live triggered status, last evaluated timestamp, and observed values.",
)
async def list_alerts_endpoint():
    alerts = await list_alerts()
    return AlertsListResponse(alerts=alerts)


@router.delete(
    "/{alert_id}",
    response_model=DeleteAlertResponse,
    summary="Delete a weather alert",
    description="Remove an existing weather alert by its identifier.",
    responses={
        404: {"model": ErrorResponse, "description": "Alert not found."},
    },
)
async def delete_alert_endpoint(
    alert_id: str = Path(..., description="Unique alert ID (e.g. alert_123).")
):
    deleted = await delete_alert(alert_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "ALERT_NOT_FOUND", "message": f"Alert '{alert_id}' was not found."}},
        )

    return DeleteAlertResponse(
        message="Alert deleted successfully.",
        id=alert_id,
    )
