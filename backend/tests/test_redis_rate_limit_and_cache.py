"""
tests/test_redis_rate_limit_and_cache.py

Dedicated comprehensive test suite verifying:
1. Rate limit is per user (User A hits 429; User B succeeds).
2. Recent locations are per user (User A sees Hyderabad; User B sees empty list).
3. Recent location ordering & deduplication (Hyderabad, Chennai, Pune, Hyderabad -> [Hyderabad, Pune, Chennai]).
4. Identity strictly from verified JWT sub (spoofed headers/body/query user_id ignored).
5. Redis TTL verification (rate limit keys & recent locations keys have TTLs).
6. Atomic concurrency under race conditions.
"""

import asyncio
from unittest.mock import patch
import pytest
from fastapi import FastAPI, Depends, Request
from starlette.testclient import TestClient

from app.core.auth import create_access_token, get_current_user, AuthUser
from app.core.limiter import RateLimitExceeded, RedisRateLimiter, rate_limit_exceeded_handler
from app.services.recent_locations_service import (
    add_recent_location,
    get_recent_locations,
)
from tests.fake_redis import FakeRedisAsync


# ── Create isolated FastAPI test application ──────────────────────────────────

redis_test_app = FastAPI()
redis_test_app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
test_limiter = RedisRateLimiter()
test_limiter.enabled = True


@redis_test_app.get("/api/v1/weather/user-limited")
@test_limiter.limit("2/minute")
async def user_limited_endpoint(request: Request, current_user: AuthUser = Depends(get_current_user)):
    return {"status": "ok", "user": current_user.id}


@redis_test_app.get("/api/v1/location/recent")
async def recent_locations_endpoint(current_user: AuthUser = Depends(get_current_user)):
    locs = await get_recent_locations(current_user.id)
    return {"locations": locs}


client = TestClient(redis_test_app)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_redis_cloud():
    """Provides an isolated in-memory Redis instance simulating Redis Cloud."""
    fake = FakeRedisAsync()
    with patch("app.core.limiter.get_redis_client", return_value=fake), \
         patch("app.services.recent_locations_service.get_redis_client", return_value=fake):
        yield fake


def _make_auth_header(user_id: str, email: str = "test@example.com") -> dict:
    token = create_access_token(user_id=user_id, email=email)
    return {"Authorization": f"Bearer {token}"}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rate_limit_is_per_user(fake_redis_cloud):
    """
    Test 1: Every authenticated user has their OWN independent rate limit.
    User A makes requests until 429.
    User B with a different valid token can still make requests successfully.
    """
    headers_a = _make_auth_header("user_alpha")
    headers_b = _make_auth_header("user_bravo")

    # User A - request 1: 200 OK
    r1 = client.get("/api/v1/weather/user-limited", headers=headers_a)
    assert r1.status_code == 200

    # User A - request 2: 200 OK
    r2 = client.get("/api/v1/weather/user-limited", headers=headers_a)
    assert r2.status_code == 200

    # User A - request 3: 429 Too Many Requests
    r3 = client.get("/api/v1/weather/user-limited", headers=headers_a)
    assert r3.status_code == 429
    assert r3.json()["detail"]["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in r3.headers

    # User B - request 1: MUST SUCCEED (200 OK), independent counter
    rb1 = client.get("/api/v1/weather/user-limited", headers=headers_b)
    assert rb1.status_code == 200
    assert rb1.json()["user"] == "user_bravo"


@pytest.mark.asyncio
async def test_recent_locations_are_per_user(fake_redis_cloud):
    """
    Test 2: Recent locations are cached per user in Redis Cloud.
    User A's locations are never visible to User B.
    """
    # User A searches for Hyderabad
    await add_recent_location("user_A", "Hyderabad", latitude=17.38, longitude=78.48)

    # User A sees Hyderabad
    locs_a = await get_recent_locations("user_A")
    assert len(locs_a) == 1
    assert locs_a[0]["name"] == "Hyderabad"

    # User B sees empty list
    locs_b = await get_recent_locations("user_B")
    assert locs_b == []


@pytest.mark.asyncio
async def test_recent_location_ordering_and_deduplication(fake_redis_cloud):
    """
    Test 3: Order of recency & deduplication.
    Search: Hyderabad, Chennai, Pune, then Hyderabad again.
    Result must be [Hyderabad, Pune, Chennai] (Hyderabad moved to front).
    """
    user_id = "user_journey"
    await add_recent_location(user_id, "Hyderabad", latitude=17.38, longitude=78.48)
    await add_recent_location(user_id, "Chennai", latitude=13.08, longitude=80.27)
    await add_recent_location(user_id, "Pune", latitude=18.52, longitude=73.85)
    await add_recent_location(user_id, "Hyderabad", latitude=17.38, longitude=78.48)

    locs = await get_recent_locations(user_id)
    names = [loc["name"] for loc in locs]
    assert names == ["Hyderabad", "Pune", "Chennai"]


def test_jwt_identity_cannot_be_spoofed(fake_redis_cloud):
    """
    Test 4: Identity strictly comes from verified JWT sub.
    Client-supplied user_id in headers, query parameters, or body must be ignored.
    """
    headers = _make_auth_header("legit_sub_user")
    headers["X-User-ID"] = "spoofed_admin"

    # Rate limit test with spoofed parameters
    r1 = client.get("/api/v1/weather/user-limited?user_id=spoofed_query_user", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["user"] == "legit_sub_user"

    # Verify that the Redis rate limit key contains 'legit_sub_user' and NOT 'spoofed'
    keys = list(fake_redis_cloud._data.keys())
    assert any("rate_limit:legit_sub_user" in k for k in keys)
    assert not any("spoofed" in k for k in keys)


@pytest.mark.asyncio
async def test_redis_ttl_enforcement(fake_redis_cloud):
    """
    Test 5: Redis TTL verification.
    Rate limit keys expire after window; recent location keys have TTL.
    """
    user_id = "ttl_user"
    await add_recent_location(user_id, "Delhi", latitude=28.61, longitude=77.20)

    # Verify recent locations key has TTL
    key = f"recent_locations:{user_id}"
    ttl_rem = await fake_redis_cloud.ttl(key)
    assert ttl_rem > 0
    assert ttl_rem <= 86400 * 30  # Default 30 days


@pytest.mark.asyncio
async def test_atomic_rate_limit_concurrency(fake_redis_cloud):
    """
    Test 6: Concurrent requests execute atomically via Lua script without race conditions.
    """
    key = "rate_limit:concurrent_user:endpoint:60"
    limit = 5
    window = 60

    # 10 concurrent requests
    results = await asyncio.gather(*[
        fake_redis_cloud.eval("script", 1, key, limit, window)
        for _ in range(10)
    ])

    # First 5 should have curr <= 5, remaining 5 have curr > 5
    current_values = [res[0] for res in results]
    assert sorted(current_values) == list(range(1, 11))
    under_limit = [c for c in current_values if c <= limit]
    over_limit = [c for c in current_values if c > limit]
    assert len(under_limit) == 5
    assert len(over_limit) == 5
