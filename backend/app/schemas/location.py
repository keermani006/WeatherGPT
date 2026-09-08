"""
app/schemas/location.py

Pydantic models for /api/v1/location/search.
"""

from typing import List
from pydantic import BaseModel, Field


class LocationSearchResult(BaseModel):
    """Normalized search result item for a geocoded place."""

    name: str = Field(description="Name or title of the place.")
    country: str = Field(default="", description="Country where the place is located.")
    latitude: float = Field(ge=-90.0, le=90.0, description="Latitude coordinate.")
    longitude: float = Field(ge=-180.0, le=180.0, description="Longitude coordinate.")


class LocationSearchResponse(BaseModel):
    """Response returned by GET /api/v1/location/search."""

    results: List[LocationSearchResult] = Field(
        default_factory=list, description="Matching locations found by Nominatim."
    )
