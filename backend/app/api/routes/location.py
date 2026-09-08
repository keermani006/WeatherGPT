"""
app/api/routes/location.py

FastAPI endpoints for location resolution and autocomplete search:
  - GET /api/v1/location/search

Phase 2: rate limiting, query length validation, and circuit breaker errors.
"""

import logging
import httpx
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status, Request

from app.core.limiter import limiter
from app.core.resilience import ServiceUnavailableError
from app.core.config import get_settings
from app.schemas.errors import ErrorResponse
from app.schemas.location import LocationSearchResponse
from app.services.location_service import search_locations

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/location", tags=["Location"])

_MAX_QUERY_LEN = 100


@router.get(
    "/search",
    response_model=LocationSearchResponse,
    summary="Search places and geocode to coordinates",
    description=(
        "Search place names and addresses via Nominatim / OpenStreetMap. "
        "Returns up to 10 matching results. Results are cached for 1 hour. "
        "Rate limited to reduce Nominatim traffic."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Empty or invalid query string."},
        429: {"description": "Rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "Geocoding service unavailable."},
        503: {"model": ErrorResponse, "description": "Geocoding service circuit open."},
    },
)
@limiter.limit(settings.rate_limit_location)
async def search_locations_endpoint(
    request: Request,
    q: Optional[str] = Query(None, min_length=1, max_length=_MAX_QUERY_LEN, description="Location search query (e.g. 'Hyderabad', 'Paris')."),
    query: Optional[str] = Query(None, min_length=1, max_length=_MAX_QUERY_LEN, description="Alternative alias for q."),
    limit: int = Query(5, ge=1, le=10, description="Max number of results to return."),
):
    if not q and not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "MISSING_QUERY", "message": "Query parameter 'q' is required."}},
        )
    search_term = (q or query or "").strip()
    if not search_term:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_QUERY", "message": "Search query cannot be empty or whitespace."}},
        )
    if len(search_term) > _MAX_QUERY_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "QUERY_TOO_LONG", "message": f"Search query must not exceed {_MAX_QUERY_LEN} characters."}},
        )

    try:
        results = await search_locations(search_term, limit=limit)
        return LocationSearchResponse(results=results)
    except ServiceUnavailableError as exc:
        logger.error("Nominatim circuit breaker open: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "GEOCODING_SERVICE_UNAVAILABLE", "message": "Geocoding service is temporarily unavailable."}},
        ) from exc
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.error("Nominatim search error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "GEOCODING_SERVICE_UNAVAILABLE", "message": "Geocoding service unavailable."}},
        ) from exc
