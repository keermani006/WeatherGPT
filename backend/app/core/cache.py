"""
app/core/cache.py

In-memory TTL cache with single-flight (request coalescing) to prevent
cache stampedes on simultaneous identical requests.

Design:
  - Zero external dependencies (uses stdlib time and asyncio only).
  - Coordinates are rounded to 2 decimal places before key generation to
    prevent excessive cache fragmentation (~1.1 km precision).
  - Each entry stores (value, expiry_monotonic).
  - get() returns None on miss or expired entry.
  - Single-flight: if two coroutines request the same key simultaneously
    while the cache is empty, the second waits for the first to populate it.

Limitations:
  - In-memory only; lost on server restart.
  - Per-process only; not suitable for horizontally-scaled deployments.
    Migrate to Redis-backed cache for multi-instance production.

NEVER cache:
  - Authentication tokens
  - Private user data (alerts are user-specific)
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class TTLCache:
    """Thread-safe (asyncio) in-memory TTL key-value cache."""

    def __init__(self) -> None:
        # {key: (value, expires_at_monotonic)}
        self._store: Dict[str, Tuple[Any, float]] = {}
        # Single-flight: pending futures keyed by cache key
        self._inflight: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def _is_alive(self, key: str) -> bool:
        """Return True if the key exists and has not expired."""
        entry = self._store.get(key)
        if entry is None:
            return False
        _, expires_at = entry
        return time.monotonic() < expires_at

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None on miss/expiry."""
        entry = self._store.get(key)
        if entry is None:
            logger.debug("Cache MISS: %s", key)
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            # Lazy eviction
            self._store.pop(key, None)
            logger.debug("Cache EXPIRED: %s", key)
            return None
        logger.debug("Cache HIT: %s", key)
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        """Store a value with a TTL in seconds."""
        if ttl <= 0:
            return
        self._store[key] = (value, time.monotonic() + ttl)
        logger.debug("Cache SET: %s (ttl=%ds)", key, ttl)

    def delete(self, key: str) -> None:
        """Remove a specific cache entry."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Evict all entries (useful in tests)."""
        self._store.clear()
        self._inflight.clear()

    def size(self) -> int:
        """Return number of alive cache entries."""
        now = time.monotonic()
        return sum(1 for _, (_, exp) in self._store.items() if now < exp)

    async def get_or_set(self, key: str, fetch_fn, ttl: int) -> Any:
        """
        Single-flight cache get-or-set.

        If the key is cached, return immediately.
        If another coroutine is already fetching the same key, wait for it.
        Otherwise, execute fetch_fn(), cache the result, and notify waiters.
        """
        # Fast path: already cached
        value = self.get(key)
        if value is not None:
            return value

        async with self._lock:
            # Double-check after acquiring lock
            value = self.get(key)
            if value is not None:
                return value

            # Another coroutine is already fetching this key
            if key in self._inflight:
                event = self._inflight[key]
                self._lock.release()
                try:
                    await asyncio.wait_for(event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("Cache single-flight timeout for key: %s", key)
                finally:
                    try:
                        await self._lock.acquire()
                    except Exception:  # noqa: BLE001
                        pass
                return self.get(key)  # May still be None if upstream failed

            # We are the first — set up the event and release the lock
            event = asyncio.Event()
            self._inflight[key] = event

        # Fetch outside the lock to allow other coroutines to proceed
        try:
            value = await fetch_fn()
            self.set(key, value, ttl)
            return value
        except Exception:
            raise
        finally:
            async with self._lock:
                self._inflight.pop(key, None)
            event.set()


# ── Module-level singleton instances per data domain ──────────────────────────

weather_cache = TTLCache()
location_cache = TTLCache()
climate_cache = TTLCache()


# ── Key builders ──────────────────────────────────────────────────────────────

def _coords(lat: float, lon: float) -> str:
    """Round coordinates to 2 decimal places (~1.1 km) for cache key."""
    return f"{lat:.2f}:{lon:.2f}"


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
    return f"geocode:{place.lower().strip()}"


def reverse_geocode_key(lat: float, lon: float) -> str:
    return f"revgeo:{_coords(lat, lon)}"
