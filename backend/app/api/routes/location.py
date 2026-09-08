"""
app/api/routes/location.py

FastAPI endpoints for location resolution and autocomplete search:
  - GET /api/v1/location/search
"""

import logging
import httpx
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.errors import ErrorResponse
from app.schemas.location import LocationSearchResponse
from app.services.location_service import search_locations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/location", tags=["Location"])


@router.get(
    "/search",
    response_model=LocationSearchResponse,
    summary="Search places and geocode to coordinates",
    description="Search place names and addresses via Nominatim / OpenStreetMap. Returns up to 10 matching results.",
    responses={
        400: {"model": ErrorResponse, "description": "Empty or invalid query string."},
        502: {"model": ErrorResponse, "description": "Geocoding service unavailable."},
    },
)
async def search_locations_endpoint(
    q: Optional[str] = Query(None, min_length=1, description="Location search query (e.g. 'Hyderabad', 'Paris')."),
    query: Optional[str] = Query(None, min_length=1, description="Alternative alias for q."),
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

    try:
        results = await search_locations(search_term, limit=limit)
        return LocationSearchResponse(results=results)
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        logger.error("Nominatim search error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "GEOCODING_SERVICE_UNAVAILABLE", "message": "Geocoding service unavailable."}},
        ) from exc
