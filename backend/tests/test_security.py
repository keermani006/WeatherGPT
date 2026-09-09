"""
tests/test_security.py

Tests for security headers and CORS configuration:
  - Security headers present on all responses (X-Request-ID, X-Content-Type-Options, etc.)
  - Unique X-Request-ID per request
  - CORS header validation
"""

import uuid
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_security_headers_present_on_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_unique_request_id_generated():
    resp1 = client.get("/health")
    resp2 = client.get("/health")

    id1 = resp1.headers.get("X-Request-ID")
    id2 = resp2.headers.get("X-Request-ID")

    assert id1 is not None
    assert id2 is not None
    assert id1 != id2

    # Verify both are valid UUID strings
    uuid.UUID(id1)
    uuid.UUID(id2)


def test_cors_preflight():
    resp = client.options(
        "/api/v1/weather/current",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
