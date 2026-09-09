"""
tests/test_location.py

Unit and integration tests for /api/v1/location/search.
"""

from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.location import LocationSearchResult

client = TestClient(app)

_MOCK_SEARCH_RESULTS = [
    LocationSearchResult(name="Hyderabad", country="India", latitude=17.385, longitude=78.4867),
    LocationSearchResult(name="Secunderabad", country="India", latitude=17.4399, longitude=78.4983),
]


class TestLocationSearch:
    def test_search_valid_location(self):
        with patch("app.api.routes.location.search_locations", AsyncMock(return_value=_MOCK_SEARCH_RESULTS)):
            resp = client.get("/api/v1/location/search?q=Hyderabad")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["results"][0]["name"] == "Hyderabad"
        assert data["results"][0]["country"] == "India"
        assert data["results"][0]["latitude"] == 17.385

    def test_search_empty_query_string(self):
        resp = client.get("/api/v1/location/search?q=")
        assert resp.status_code == 422  # min_length=1 in Query

    def test_search_whitespace_query(self):
        resp = client.get("/api/v1/location/search?q=%20%20%20")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_QUERY"

    def test_search_unknown_location_empty_results(self):
        with patch("app.api.routes.location.search_locations", AsyncMock(return_value=[])):
            resp = client.get("/api/v1/location/search?q=NoSuchPlaceOnEarth123XYZ")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_nominatim_timeout(self):
        with patch(
            "app.api.routes.location.search_locations",
            AsyncMock(side_effect=httpx.TimeoutException("Nominatim timed out")),
        ):
            resp = client.get("/api/v1/location/search?q=Paris")
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "GEOCODING_SERVICE_UNAVAILABLE"
