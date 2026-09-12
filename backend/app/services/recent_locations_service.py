"""
app/services/recent_locations_service.py

Redis Cloud-backed user-specific recent locations cache.
Guarantees complete user isolation: User A never sees User B's locations.
Maintains deduplicated recency ordering (most recent first) capped at 10 items.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)


def _make_key(user_id: str) -> str:
    """Format Redis key isolating recent locations per user."""
    clean_id = user_id.strip()
    return f"recent_locations:{clean_id}"


def _normalize_location_name(name: str) -> str:
    """Normalize place name for consistent deduplication."""
    return name.strip().title()


async def add_recent_location(
    user_id: str,
    location_name: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    country: Optional[str] = None,
) -> bool:
    """
    Add or update a location in the authenticated user's recent locations list.
    Moves the location to the front, removes any duplicates, caps at recent_locations_max,
    and refreshes the TTL in Redis.
    """
    if not user_id or not location_name or not location_name.strip():
        return False

    settings = get_settings()
    norm_name = _normalize_location_name(location_name)
    key = _make_key(user_id)
    max_items = settings.recent_locations_max
    ttl = settings.recent_locations_ttl

    redis = get_redis_client()
    if redis is None:
        logger.debug("Redis client unavailable; skipped adding recent location for user %s", user_id)
        return False

    entry_data = {
        "name": norm_name,
        "latitude": round(latitude, 4) if latitude is not None else None,
        "longitude": round(longitude, 4) if longitude is not None else None,
        "country": country.strip() if country else None,
    }

    try:
        # Fetch current list
        raw_items = await redis.lrange(key, 0, -1)
        parsed_items: List[Dict[str, Any]] = []

        for item_str in raw_items:
            try:
                item_obj = json.loads(item_str)
                # Filter out previous occurrence with same normalized name (deduplication)
                if item_obj.get("name", "").strip().lower() != norm_name.lower():
                    parsed_items.append(item_obj)
            except Exception:  # noqa: BLE001
                # Support plain string entries if any
                if item_str.strip().lower() != norm_name.lower():
                    parsed_items.append({"name": item_str.strip().title()})

        # Insert new/updated location at the head (most recent)
        new_list = [entry_data] + parsed_items
        capped_list = new_list[:max_items]

        # Atomically rewrite list and refresh TTL using pipeline
        async with redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if capped_list:
                pipe.rpush(key, *[json.dumps(x) for x in capped_list])
                pipe.expire(key, ttl)
            await pipe.execute()

        logger.debug("Updated recent locations for user %s: %s", user_id, norm_name)
        return True
    except Exception as exc:  # noqa: BLE001
        # Graceful degradation on Redis failure
        logger.warning("Failed to update recent locations in Redis (%s)", exc)
        return False


async def get_recent_locations(user_id: str) -> List[Dict[str, Any]]:
    """
    Retrieve the recent locations list for a specific authenticated user.
    Returns an empty list if user has no recent locations or if Redis is offline.
    """
    if not user_id:
        return []

    key = _make_key(user_id)
    redis = get_redis_client()
    if redis is None:
        logger.debug("Redis client unavailable; returning empty recent locations for %s", user_id)
        return []

    try:
        raw_items = await redis.lrange(key, 0, -1)
        results: List[Dict[str, Any]] = []
        for item_str in raw_items:
            try:
                results.append(json.loads(item_str))
            except Exception:
                results.append({"name": item_str})
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch recent locations from Redis (%s)", exc)
        return []


async def clear_recent_locations(user_id: str) -> bool:
    """Clear recent locations for a specific user."""
    if not user_id:
        return False
    key = _make_key(user_id)
    redis = get_redis_client()
    if redis is None:
        return False
    try:
        await redis.delete(key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to clear recent locations in Redis (%s)", exc)
        return False
