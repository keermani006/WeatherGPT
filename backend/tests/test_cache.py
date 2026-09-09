"""
tests/test_cache.py

Tests for the in-memory TTLCache and single-flight request coalescing:
  - Cache set and get (hit vs miss)
  - Expiry: values expire after TTL
  - Lazy eviction
  - delete and clear
  - size calculation
  - Single-flight: concurrent fetches only invoke upstream once
  - Cache key generation and coordinate rounding
"""

import asyncio
import time
import pytest

from app.core.cache import (
    TTLCache,
    current_weather_key,
    forecast_key,
    hourly_key,
    climate_key,
    location_search_key,
)


@pytest.mark.asyncio
async def test_cache_set_and_get():
    cache = TTLCache()
    cache.set("key1", "val1", ttl=60)
    assert cache.get("key1") == "val1"
    assert cache.get("key_missing") is None


@pytest.mark.asyncio
async def test_cache_expiry():
    cache = TTLCache()
    # TTL of 1 second
    cache.set("short_lived", "data", ttl=1)
    assert cache.get("short_lived") == "data"
    # Wait for expiration
    await asyncio.sleep(1.05)
    assert cache.get("short_lived") is None


@pytest.mark.asyncio
async def test_cache_zero_or_negative_ttl():
    cache = TTLCache()
    cache.set("no_ttl", "val", ttl=0)
    assert cache.get("no_ttl") is None
    cache.set("neg_ttl", "val", ttl=-10)
    assert cache.get("neg_ttl") is None


@pytest.mark.asyncio
async def test_cache_delete_and_clear():
    cache = TTLCache()
    cache.set("a", 1, ttl=60)
    cache.set("b", 2, ttl=60)
    assert cache.size() == 2

    cache.delete("a")
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.size() == 1

    cache.clear()
    assert cache.get("b") is None
    assert cache.size() == 0


@pytest.mark.asyncio
async def test_single_flight_coalescing():
    cache = TTLCache()
    call_count = 0

    async def expensive_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "computed_data"

    # Launch 5 concurrent calls for the same key
    tasks = [
        cache.get_or_set("heavy_key", expensive_fetch, ttl=60)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # All 5 should receive the computed data
    for r in results:
        assert r == "computed_data"

    # Upstream function should have been executed exactly once
    assert call_count == 1


def test_cache_key_generation():
    # Verify coordinate rounding (~1.1 km precision at 2 decimals)
    k1 = current_weather_key(17.38501, 78.48671)
    k2 = current_weather_key(17.38509, 78.48674)
    assert k1 == k2
    assert k1 == "weather:current:17.39:78.49"

    f_key = forecast_key(17.385, 78.4867, 5)
    assert f_key == "weather:forecast:17.39:78.49:5"

    h_key = hourly_key(17.385, 78.4867, "2026-09-08")
    assert h_key == "weather:hourly:17.39:78.49:2026-09-08"

    c_key = climate_key(17.385, 78.4867, "2025-01-01", "2025-01-30", "CMCC_CM2_VHR4")
    assert c_key == "climate:17.39:78.49:2025-01-01:2025-01-30:CMCC_CM2_VHR4"

    loc_key = location_search_key(" Hyderabad  ", 5)
    assert loc_key == "location:hyderabad:5"
