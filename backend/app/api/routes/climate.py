"""
app/api/routes/climate.py

FastAPI endpoint for climate information using Open-Meteo Climate API (CMIP6):
  - GET /api/v1/climate
"""

import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, status

from app.core.resilience import ServiceUnavailableError
from app.schemas.climate import ClimateResponse
from app.schemas.errors import ErrorResponse
from app.services.climate_service import (
    DEFAULT_END_DATE,
    DEFAULT_START_DATE,
    SUPPORTED_CLIMATE_MODELS,
    get_climate_data,
)
from app.services.location_service import reverse_geocode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/climate", tags=["Climate"])

_SUPPORTED_MODELS_STR = ", ".join(sorted(SUPPORTED_CLIMATE_MODELS))


@router.get(
    "",
    response_model=ClimateResponse,
    summary="Get climate data and CMIP6 projections",
    description=(
        "Retrieve CMIP6 climate model projections (temperature, precipitation, humidity, wind) "
        "powered by the Open-Meteo Climate API. Computes mathematical summaries and trends.\n\n"
        f"Supported models: {_SUPPORTED_MODELS_STR}"
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid dates, date range, or unsupported model."},
        422: {"description": "Validation error for coordinate bounds."},
        502: {"model": ErrorResponse, "description": "Open-Meteo Climate API error."},
        503: {"model": ErrorResponse, "description": "Open-Meteo Climate API timeout or circuit open."},
    },
)
async def climate_endpoint(
    latitude: float = Query(..., ge=-90.0, le=90.0, description="Latitude coordinate."),
    longitude: float = Query(..., ge=-180.0, le=180.0, description="Longitude coordinate."),
    start_date: Optional[str] = Query(
        None, description=f"Start date in YYYY-MM-DD format (default: {DEFAULT_START_DATE})."
    ),
    end_date: Optional[str] = Query(
        None, description=f"End date in YYYY-MM-DD format (default: {DEFAULT_END_DATE})."
    ),
    model: Optional[str] = Query(
        None,
        description=f"CMIP6 model name. Supported: {_SUPPORTED_MODELS_STR}.",
    ),
):
    s_date = start_date or DEFAULT_START_DATE
    e_date = end_date or DEFAULT_END_DATE

    # Validate model against known list
    if model and model not in SUPPORTED_CLIMATE_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "UNSUPPORTED_CLIMATE_MODEL",
                    "message": f"Model '{model}' is not supported. Supported: {_SUPPORTED_MODELS_STR}.",
                }
            },
        )

    try:
        d_start = datetime.strptime(s_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_DATE_FORMAT", "message": "start_date must be in YYYY-MM-DD format."}},
        )

    try:
        d_end = datetime.strptime(e_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_DATE_FORMAT", "message": "end_date must be in YYYY-MM-DD format."}},
        )

    if d_start > d_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_DATE_RANGE", "message": "start_date cannot be after end_date."}},
        )

    if (d_end - d_start).days > 3650:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "DATE_RANGE_TOO_LARGE", "message": "Climate query date range cannot exceed 10 years (3650 days)."}},
        )

    try:
        location_name = await reverse_geocode(latitude, longitude)
    except Exception:  # noqa: BLE001
        location_name = f"Location ({latitude:.2f}, {longitude:.2f})"

    try:
        return await get_climate_data(
            latitude=latitude,
            longitude=longitude,
            location_name=location_name,
            start_date=s_date,
            end_date=e_date,
            model=model,
        )
    except ServiceUnavailableError as exc:
        logger.error("Climate API circuit breaker open: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "CLIMATE_SERVICE_UNAVAILABLE", "message": "Climate service is temporarily unavailable."}},
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error("Open-Meteo Climate API timeout: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "CLIMATE_SERVICE_TIMEOUT", "message": "Open-Meteo Climate API timed out."}},
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Open-Meteo Climate API HTTP error %d: %s", exc.response.status_code, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "CLIMATE_SERVICE_ERROR", "message": "Open-Meteo Climate API returned an upstream error."}},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error in climate endpoint: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "CLIMATE_SERVICE_ERROR", "message": "Failed to retrieve climate model data."}},
        ) from exc
