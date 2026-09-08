"""
tests/test_health.py

Tests for health and liveness endpoints:
  - GET /health
  - GET /healthz
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_canonical_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_legacy_healthz_endpoint():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
