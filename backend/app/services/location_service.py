"""
app/services/location_service.py

Resolve location coordinates and place name via Nominatim / OpenStreetMap.

Phase 2 additions:
  - TTL caching for geocode and reverse-geocode results (1 hour)
  - Retry with exponential backoff on transient Nominatim failures
  - Circuit breaker to stop hammering Nominatim when it's down
  - Query validation (empty / too-long queries rejected before hitting Nominatim)
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.core.cache import (
    geocode_key,
    location_cache,
    location_search_key,
    reverse_geocode_key,
)
from app.core.config import get_settings
from app.core.resilience import async_retry, cb_nominatim
from app.schemas.location import LocationSearchResult

logger = logging.getLogger(__name__)
settings = get_settings()

# Maximum query length to avoid sending uncontrolled traffic to Nominatim
_MAX_QUERY_LENGTH = 100


@dataclass
class ResolvedLocation:
    """Resolved location coordinates and display name."""
    name: str
    latitude: float
    longitude: float
    source: str  # "explicit", "extracted", or "gps"


_STOP_WORDS = {
    "here", "there", "today", "tomorrow", "tonight", "now",
    "the morning", "this morning", "the afternoon", "this afternoon",
    "the evening", "this evening", "the weekend", "this weekend",
}


def extract_location_from_message(message: str | None) -> Optional[str]:
    """Extract a place name from questions like 'weather in Chennai' or standalone 'Paris'."""
    if not message:
        return None
    cleaned = message.strip().rstrip("?.!").strip()
    match = re.search(
        r"\b(?:in|at|for)\s+([A-Za-z\s,-]+?)(?:\s+(?:today|tomorrow|tonight|now|this\s+\w+|next\s+\w+))?$",
        cleaned,
        re.IGNORECASE,
    )
    if match:
        loc = match.group(1).strip()
        if loc.lower() not in _STOP_WORDS:
            return loc

    # Standalone 1-3 word place name candidate (e.g. 'London', 'Paris', 'San Francisco', 'New York')
    words = cleaned.split()
    if 1 <= len(words) <= 3:
        words_lower = set(w.lower() for w in words)
        _EXCLUDED = {
            "weather", "forecast", "rain", "temperature", "temp", "today", "tomorrow",
            "tonight", "now", "what", "how", "is", "will", "write", "code", "python",
            "tell", "joke", "explain", "assignment", "who", "why", "hello", "hi", "hey"
        }
        if not (words_lower & _STOP_WORDS) and not (words_lower & _EXCLUDED):
            return cleaned

    return None




async def geocode_location(place_name: str) -> tuple[float, float, str]:
    """
    Geocode a place name into (latitude, longitude, display_name) using Nominatim.
    Results are cached for 1 hour.
    """
    place_name = place_name.strip()[:_MAX_QUERY_LENGTH]
    cache_key = geocode_key(place_name)

    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"q": place_name, "format": "json", "limit": 1}
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    async def _do_request():
        async with cb_nominatim:
            async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

    logger.info("Geocoding place '%s' via Nominatim", place_name)
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )

    if not data:
        logger.warning("Nominatim found no matches for '%s'", place_name)
        raise ValueError(f"Location not found: '{place_name}'")

    first = data[0]
    lat = float(first["lat"])
    lon = float(first["lon"])
    display_name = (
        first.get("name") or first.get("display_name", place_name).split(",")[0].strip()
    )
    logger.info("Nominatim resolved '%s' → lat=%.4f, lon=%.4f", place_name, lat, lon)

    result = (lat, lon, display_name)
    location_cache.set(cache_key, result, settings.location_cache_ttl)
    return result


async def reverse_geocode(latitude: float, longitude: float) -> str:
    """
    Reverse geocode GPS coordinates to a human-friendly place name.
    Results are cached for 1 hour.
    Falls back gracefully on failure.
    """
    cache_key = reverse_geocode_key(latitude, longitude)
    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {"lat": latitude, "lon": longitude, "format": "json"}
    url = f"{settings.nominatim_base_url.rstrip('/')}/reverse"

    try:
        async def _do_request():
            async with cb_nominatim:
                async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp.json()

        data = await async_retry(
            _do_request,
            max_attempts=2,  # fewer retries for reverse geocode (best-effort)
            base_delay=settings.retry_base_delay,
        )
        address = data.get("address", {})
        name = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("suburb")
            or address.get("county")
            or data.get("name")
        )
        if name:
            location_cache.set(cache_key, name, settings.location_cache_ttl)
            return name
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nominatim reverse geocoding failed (%s); using coordinate fallback", exc)

    fallback = f"Current Location ({latitude:.2f}, {longitude:.2f})"
    return fallback


async def search_locations(query: str, limit: int = 5) -> List[LocationSearchResult]:
    """
    Search places matching query via Nominatim /search.
    Results are cached for 1 hour.
    """
    cleaned = query.strip()[:_MAX_QUERY_LENGTH]
    if not cleaned:
        return []

    cache_key = location_search_key(cleaned, limit)
    cached = location_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.nominatim_user_agent}
    params = {
        "q": cleaned,
        "format": "json",
        "limit": max(1, min(limit, 10)),
        "addressdetails": 1,
    }
    url = f"{settings.nominatim_base_url.rstrip('/')}/search"

    async def _do_request():
        async with cb_nominatim:
            async with httpx.AsyncClient(timeout=settings.geo_timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

    logger.info("Searching places for '%s' via Nominatim (limit=%d)", cleaned, limit)
    data = await async_retry(
        _do_request,
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )

    results: List[LocationSearchResult] = []
    for item in data:
        display = item.get("name") or item.get("display_name", "").split(",")[0].strip()
        address = item.get("address", {})
        country = address.get("country", "")
        try:
            results.append(LocationSearchResult(
                name=display,
                country=country,
                latitude=float(item["lat"]),
                longitude=float(item["lon"]),
            ))
        except (KeyError, ValueError):
            continue

    location_cache.set(cache_key, results, settings.location_cache_ttl)
    return results


async def resolve_location(
    explicit: Optional[str] = None,
    message: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[ResolvedLocation]:
    """
    Resolve coordinates following priority rules:
      1. Explicit location from request or extracted from query (ignoring placeholder labels).
      2. Browser GPS coordinates.
      3. Neither available → return None.
    """
    _PLACEHOLDER_NAMES = {
        "current location", "current", "my location", "here",
        "device location", "gps", "unknown", "string"
    }

    clean_explicit = explicit.strip() if explicit else ""
    if clean_explicit.lower() in _PLACEHOLDER_NAMES:
        clean_explicit = ""

    if clean_explicit:
        loc_name = clean_explicit[:_MAX_QUERY_LENGTH]
        lat, lon, display_name = await geocode_location(loc_name)
        return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="explicit")

    extracted = extract_location_from_message(message)
    if extracted:
        lat, lon, display_name = await geocode_location(extracted)
        return ResolvedLocation(name=display_name, latitude=lat, longitude=lon, source="extracted")

    if latitude is not None and longitude is not None:
        display_name = await reverse_geocode(latitude, longitude)
        return ResolvedLocation(name=display_name, latitude=latitude, longitude=longitude, source="gps")

    logger.info("No explicit location or browser GPS coordinates available")
    return None

