"""
tests/test_rate_limit.py

Tests for slowapi rate limiting and 429 handler:
  - Exceeding endpoint rate limit returns 429
  - Standard error envelope returned (RATE_LIMIT_EXCEEDED)
  - Retry-After header present
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from app.core.limiter import limiter, rate_limit_exceeded_handler

rate_test_app = FastAPI()
rate_test_app.state.limiter = limiter
rate_test_app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


@rate_test_app.get("/limited")
@limiter.limit("2/minute")
async def limited_endpoint(request: Request):
    return {"message": "success"}


rate_client = TestClient(rate_test_app)


def test_rate_limit_triggers_429():
    # Clear any previous counts in limiter storage if possible
    try:
        limiter.reset()
    except Exception:
        pass

    # First request: 200
    r1 = rate_client.get("/limited")
    assert r1.status_code == 200

    # Second request: 200
    r2 = rate_client.get("/limited")
    assert r2.status_code == 200

    # Third request: 429
    r3 = rate_client.get("/limited")
    assert r3.status_code == 429
    data = r3.json()
    assert "detail" in data
    assert data["detail"]["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in r3.headers
