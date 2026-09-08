"""
app/services/location_service.py

Single responsibility: resolve location coordinates and place name.

Stack:
  - Explicit place name / query extraction → Geocoded via Nominatim (OpenStreetMap).
  - User GPS coordinates → Received from browser Geolocation API, reverse geocoded via Nominatim.

Location Priority Logic:
  1. Explicit location provided in request or extracted from user query (e.g. "weather in Chennai").
  2. Browser GPS coordinates (latitude, longitude) provided in request body.
  3. If neither is available, returns None so callers can prompt the user.

No hardcoded coordinates, no default fallback city, no IP-based guessing.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.core.config import get_settings
from app.schemas.location import LocationSearchResult

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ResolvedLocation:
    """Resolved location coordinates and display name."""

    name: str
    latitude: float
    longitude: float
    source: str  # "explicit", "extracted", or "gps"


# Non-location words that might follow 'in', 'at', or 'for'
_STOP_WORDS = {
    "here",
    "there",
    "today",
    "tomorrow",
    "tonight",
    "now",
    "the morning",
    "this morning",
    "the afternoon",
    "this afternoon",
    "the evening",
    "this evening",
    "the weekend",
    "this weekend",
}


def extract_location_from_message(message: str | None) -> Optional[str]:
    """
    Extract a place name from questions like 'weather in Chennai',
    'will it rain in Hyderabad tomorrow', 'forecast for Tokyo'.
    """
    if not message:
        return None

    cleaned = message.strip().rstrip("?.!").strip()

    # Pattern: 'in/at/for <Place>' possibly followed by time words at the end
    match = re.search(
        r"\b(?:in|at|for)\s+([A-Za-z\s,-]+?)(?:\s+(?:today|tomorrow|tonight|now|this\s+\w+|next\s+\w+))?$",
        cleaned,
        re.IGNORECASE,
    )
    if match:
        loc = match.group(1).strip()
        if loc.lower() not in _STOP_WORDS:
            return loc

    return None


async def geocode_location(place_name: str) -> tuple[float, float, str]:
    """
    Geocode a place name into (latitude, longitude, display_name) using Nominatim.

    Raises
    ------
    ValueError: If location is not found.
    httpx.TimeoutException / httpx.HTTPStatusError: On upstream failure.
    """
    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"q": place_name, "format": "json", "limit": 1}
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    logger.info("Geocoding place '%s' via Nominatim", place_name)

    async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if not data:
        logger.warning("Nominatim found no matches for '%s'", place_name)
        raise ValueError(f"Location not found: '{place_name}'")

    first = data[0]
    lat = float(first["lat"])
    lon = float(first["lon"])

    # Build a concise friendly name (e.g. "Chennai" rather than the full 100-char address)
    display_name = first.get("name") or first.get("display_name", place_name).split(",")[0].strip()
    logger.info("Nominatim resolved '%s' -> lat=%.4f, lon=%.4f, name='%s'", place_name, lat, lon, display_name)
    return lat, lon, display_name


async def reverse_geocode(latitude: float, longitude: float) -> str:
    """
    Reverse geocode GPS coordinates to a human-friendly place name using Nominatim.
    Falls back gracefully to 'Current Location' if reverse geocoding is slow or fails.
    """
    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"lat": latitude, "lon": longitude, "format": "json"}
    url = f"{settings.nominatim_base_url.rstrip('/')}/reverse"

    try:
        async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        address = data.get("address", {})
        # Prefer city -> town -> village -> suburb -> display_name
        name = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("suburb")
            or address.get("county")
            or data.get("name")
        )
        if name:
            return name
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nominatim reverse geocoding failed (%s); using generic coordinate name", exc)

    return f"Current Location ({latitude:.2f}, {longitude:.2f})"


async def search_locations(query: str, limit: int = 5) -> List[LocationSearchResult]:
    """
    Search places matching query via Nominatim /search.
    Returns normalized LocationSearchResult list.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {
        "q": cleaned,
        "format": "json",
        "limit": max(1, min(limit, 10)),
        "addressdetails": 1,
    }
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    logger.info("Searching places for '%s' via Nominatim (limit=%d)", cleaned, limit)

    async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    results: List[LocationSearchResult] = []
    for item in data:
        display = item.get("name") or item.get("display_name", "").split(",")[0].strip()
        address = item.get("address", {})
        country = address.get("country", "")

        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
            results.append(
                LocationSearchResult(
                    name=display,
                    country=country,
                    latitude=lat,
                    longitude=lon,
                )
            )
        except (KeyError, ValueError):
            continue

    return results


async def resolve_location(
    explicit: Optional[str] = None,
    message: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[ResolvedLocation]:
    """
    Resolve coordinates following the priority rules:
      1. Explicit location from request or extracted from query -> geocode via Nominatim.
      2. Browser GPS coordinates from request body -> reverse geocode via Nominatim.
      3. Neither available -> return None (prompt user for location).
    """
    # Priority 1: Explicit location in request
    if explicit and explicit.strip():
        loc_name = explicit.strip()
        lat, lon, display_name = await geocode_location(loc_name)
        return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="explicit")

    # Priority 1b: Explicit location in message (e.g. "weather in Chennai")
    extracted = extract_location_from_message(message)
    if extracted:
        lat, lon, display_name = await geocode_location(extracted)
        return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="extracted")

    # Priority 2: Browser GPS coordinates
    if latitude is not None and longitude is not None:
        display_name = await reverse_geocode(latitude, longitude)
        return ResolvedLocation(name=display_name, latitude=latitude, longitude=longitude, source="gps")

    # Priority 3: No location information available
    logger.info("No explicit location or browser GPS coordinates available")
    return None
