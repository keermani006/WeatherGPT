"""
app/api/routes/alerts.py

FastAPI endpoints for meteorological threshold alerts.

Authentication:
  POST /api/v1/alerts          — requires Bearer token (user-owned)
  GET  /api/v1/alerts          — requires Bearer token (own alerts only)
  DELETE /api/v1/alerts/{id}   — requires Bearer token + ownership check
  POST /api/v1/alerts/evaluate — public (background evaluation trigger)

Rate limiting applied to POST and evaluate endpoints.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.core.auth import AuthUser, get_current_user
from app.core.limiter import limiter
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
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.post(
    "/evaluate",
    response_model=AlertBatchEvaluationResponse,
    summary="Evaluate all active weather alerts",
    description=(
        "Immediately evaluate all active alerts against real-time Open-Meteo weather data. "
        "Groups alerts by location to minimise upstream API calls. "
        "This endpoint is public but rate-limited."
    ),
    responses={
        429: {"description": "Rate limit exceeded."},
    },
)
@limiter.limit(settings.rate_limit_alerts)
async def evaluate_alerts_endpoint(request: Request):
    return await evaluate_all_alerts()


@router.post(
    "",
    response_model=AlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new weather alert",
    description=(
        "Register a meteorological threshold alert. "
        "Requires authentication — alert will be associated with the authenticated user. "
        "The user_id is taken from the verified JWT, never from the request body."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid threshold or condition parameters."},
        401: {"description": "Missing or invalid authentication token."},
        422: {"description": "Validation error."},
        429: {"description": "Rate limit exceeded."},
    },
)
@limiter.limit(settings.rate_limit_alerts)
async def create_alert_endpoint(
    request: Request,
    alert_in: AlertRequest,
    user: AuthUser = Depends(get_current_user),
):
    if not alert_in.location_name:
        try:
            alert_in.location_name = await reverse_geocode(alert_in.latitude, alert_in.longitude)
        except Exception:  # noqa: BLE001
            alert_in.location_name = f"Location ({alert_in.latitude:.2f}, {alert_in.longitude:.2f})"

    return await create_alert(alert_in, user_id=user.id)


@router.get(
    "",
    response_model=AlertsListResponse,
    summary="List authenticated user's weather alerts",
    description="Retrieve all weather alerts belonging to the authenticated user.",
    responses={
        401: {"description": "Missing or invalid authentication token."},
    },
)
async def list_alerts_endpoint(
    user: AuthUser = Depends(get_current_user),
):
    alerts = await list_alerts(user_id=user.id)
    return AlertsListResponse(alerts=alerts)


@router.delete(
    "/{alert_id}",
    response_model=DeleteAlertResponse,
    summary="Delete a weather alert",
    description=(
        "Remove an existing weather alert. "
        "Requires authentication. Only the owning user can delete their alerts."
    ),
    responses={
        401: {"description": "Missing or invalid authentication token."},
        403: {"description": "Alert belongs to a different user."},
        404: {"model": ErrorResponse, "description": "Alert not found."},
    },
)
async def delete_alert_endpoint(
    alert_id: str = Path(..., description="Unique alert ID (e.g. alert_abc12345)."),
    user: AuthUser = Depends(get_current_user),
):
    try:
        deleted = await delete_alert(alert_id, user_id=user.id)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": str(exc)}},
        )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "ALERT_NOT_FOUND", "message": f"Alert '{alert_id}' was not found."}},
        )

    return DeleteAlertResponse(message="Alert deleted successfully.", id=alert_id)
