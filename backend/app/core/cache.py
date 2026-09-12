"""
app/core/cache.py

Redis Cloud-backed TTL cache.
Replaces in-memory storage with centralized Redis Cloud,
ensuring consistent cache state across all horizontally scaled backend workers/instances.
No global authoritative Python dictionary remains.
"""

import asyncio
import logging
import pickle
from typing import Any, Callable, Optional

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)


class TTLCache:
    """Redis Cloud-backed TTL key-value cache."""

    def __init__(self, prefix: str = "cache") -> None:
        self.prefix = prefix
        self._inflight: dict[str, asyncio.Future] = {}

    def _key(self, key: str) -> str:
        if self.prefix and not key.startswith(f"{self.prefix}:"):
            return f"{self.prefix}:{key}"
        return key

    def _stale_key(self, key: str) -> str:
        k = self._key(key)
        return f"stale:{k}"

    async def size(self) -> int:
        """Count active non-stale keys for this cache prefix in Redis."""
        redis = get_redis_client()
        if redis is None:
            return 0
        try:
            pattern = f"{self.prefix}:*" if self.prefix else "*"
            keys = await redis.keys(pattern)
            return len([k for k in keys if not k.startswith("stale:")])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache size error: %s", exc)
            return 0

    async def get(self, key: str) -> Optional[Any]:
        """Fetch cached item from Redis. Returns None on miss, expiry, or Redis outage."""
        redis = get_redis_client()
        if redis is None:
            return None
        try:
            val = await redis.get(self._key(key))
            if val is None:
                return None
            raw = val.encode("latin1") if isinstance(val, str) else val
            return pickle.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache get error for key %s: %s", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Store value in Redis Cloud with TTL and keep a stale copy for fallback."""
        if ttl <= 0:
            return
        redis = get_redis_client()
        if redis is None:
            return
        try:
            pickled = pickle.dumps(value).decode("latin1")
            r_key = self._key(key)
            stale_key = self._stale_key(key)
            async with redis.pipeline(transaction=True) as pipe:
                pipe.setex(r_key, ttl, pickled)
                # Keep stale copy for 7 days (stale-if-error fallback)
                pipe.setex(stale_key, 86400 * 7, pickled)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis cache set error for key %s: %s", key, exc)

    async def get_stale(self, key: str) -> Optional[Any]:
        """Return stale cached value from Redis if upstream API fails."""
        redis = get_redis_client()
        if redis is None:
            return None
        try:
            val = await redis.get(self._stale_key(key))
            if val is None:
                return None
            raw = val.encode("latin1") if isinstance(val, str) else val
            return pickle.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache get_stale error for key %s: %s", key, exc)
            return None

    async def delete(self, key: str) -> None:
        """Remove entry from Redis."""
        redis = get_redis_client()
        if redis is None:
            return
        try:
            await redis.delete(self._key(key), self._stale_key(key))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache delete error: %s", exc)

    async def clear_async(self) -> None:
        """Async clear entries for this cache prefix from Redis."""
        redis = get_redis_client()
        if redis is None:
            return
        try:
            pattern = f"{self.prefix}:*"
            stale_pattern = f"stale:{self.prefix}:*"
            keys = await redis.keys(pattern)
            stale_keys = await redis.keys(stale_pattern)
            all_keys = keys + stale_keys
            if all_keys:
                await redis.delete(*all_keys)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache clear error: %s", exc)

    def clear(self) -> None:
        """Clear cache entries. Supports sync calling in pytest fixtures."""
        redis = get_redis_client()
        if redis is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.clear_async())
        except RuntimeError:
            asyncio.run(self.clear_async())

    async def get_or_set(self, key: str, fetch_fn: Callable, ttl: int) -> Any:
        """
        Fetch from Redis; on cache miss, execute fetch_fn(), store in Redis, and return.
        Falls back to stale cached value on upstream exception.
        Coalesces concurrent requests for the same key.
        """
        val = await self.get(key)
        if val is not None:
            return val

        if key in self._inflight:
            return await self._inflight[key]

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight[key] = fut

        try:
            result = await fetch_fn()
            await self.set(key, result, ttl)
            fut.set_result(result)
            return result
        except Exception as exc:
            stale = await self.get_stale(key)
            if stale is not None:
                logger.warning("Upstream fetch failed for '%s' (%s); serving STALE cached data from Redis", key, exc)
                fut.set_result(stale)
                return stale
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)


# ── Domain-specific singleton instances backed by Redis Cloud ─────────────────

weather_cache = TTLCache(prefix="weather")
location_cache = TTLCache(prefix="loc")
climate_cache = TTLCache(prefix="climate")


# ── Key builders ──────────────────────────────────────────────────────────────

def _coords(lat: float, lon: float) -> str:
    """Round coordinates to 2 decimal places (~1.1 km) for cache key."""
    return f"{lat:.2f}:{lon:.2f}"


def bundle_weather_key(lat: float, lon: float) -> str:
    return f"weather:bundle:{_coords(lat, lon)}"


def current_weather_key(lat: float, lon: float) -> str:
    return f"weather:current:{_coords(lat, lon)}"


def forecast_key(lat: float, lon: float, days: int) -> str:
    return f"weather:forecast:{_coords(lat, lon)}:{days}"


def hourly_key(lat: float, lon: float, date: str) -> str:
    return f"weather:hourly:{_coords(lat, lon)}:{date}"


def climate_key(lat: float, lon: float, start: str, end: str, model: str) -> str:
    return f"climate:{_coords(lat, lon)}:{start}:{end}:{model}"


def location_search_key(query: str, limit: int) -> str:
    return f"location:{query.lower().strip()}:{limit}"


def geocode_key(place: str) -> str:
    return f"location:geocode:{place.lower().strip()}"


def reverse_geocode_key(lat: float, lon: float) -> str:
    return f"location:revgeo:{_coords(lat, lon)}"

