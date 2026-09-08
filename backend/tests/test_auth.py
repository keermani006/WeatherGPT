"""
tests/test_auth.py

Tests for Supabase JWT verification, AuthUser identity, and authentication flows:
  - Valid JWT with correct HS256 signature resolves to AuthUser
  - Expired JWT returns 401 with TOKEN_EXPIRED
  - Malformed or tampered JWT returns 401 with INVALID_TOKEN
  - Missing Authorization header returns 401 with MISSING_TOKEN
  - Missing 'sub' claim returns 401
  - Unconfigured SUPABASE_JWT_SECRET returns 503 with AUTH_NOT_CONFIGURED
  - Protected route integration test
"""

import time
from unittest.mock import patch
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import AuthUser, get_current_user, _decode_jwt
from app.core.config import get_settings

TEST_SECRET = "test-jwt-secret-key-32-chars-long!!"


def _make_token(payload: dict, secret: str = TEST_SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def test_auth_user_class():
    user = AuthUser(user_id="user-123", email="user@example.com", role="authenticated")
    assert user.id == "user-123"
    assert user.email == "user@example.com"
    assert user.role == "authenticated"
    assert repr(user) == "AuthUser(id='user-123')"


def test_decode_valid_jwt():
    exp = int(time.time()) + 3600
    token = _make_token({"sub": "user-456", "email": "test@domain.com", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        payload = _decode_jwt(token)
        assert payload["sub"] == "user-456"
        assert payload["email"] == "test@domain.com"


def test_decode_expired_jwt():
    exp = int(time.time()) - 3600
    token = _make_token({"sub": "user-456", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        with pytest.raises(jwt.ExpiredSignatureError):
            _decode_jwt(token)


def test_decode_invalid_signature():
    exp = int(time.time()) + 3600
    token = _make_token({"sub": "user-456", "exp": exp}, secret="wrong-secret-key-32-chars-long!!")
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        with pytest.raises(jwt.InvalidSignatureError):
            _decode_jwt(token)


def test_decode_missing_secret():
    exp = int(time.time()) + 3600
    token = _make_token({"sub": "user-456", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", ""):
        with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET is not configured"):
            _decode_jwt(token)


# ── Integration tests with test FastAPI app ────────────────────────────────────

auth_test_app = FastAPI()

@auth_test_app.get("/test-protected")
async def protected_endpoint(user: AuthUser = Depends(get_current_user)):
    return {"user_id": user.id, "email": user.email, "role": user.role}

auth_test_client = TestClient(auth_test_app)


def test_protected_route_success():
    exp = int(time.time()) + 3600
    token = _make_token({"sub": "user-789", "email": "alice@example.com", "role": "authenticated", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        resp = auth_test_client.get("/test-protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {
            "user_id": "user-789",
            "email": "alice@example.com",
            "role": "authenticated",
        }


def test_protected_route_missing_token():
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        resp = auth_test_client.get("/test-protected")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "MISSING_TOKEN"


def test_protected_route_expired_token():
    exp = int(time.time()) - 100
    token = _make_token({"sub": "user-789", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        resp = auth_test_client.get("/test-protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "TOKEN_EXPIRED"


def test_protected_route_invalid_token():
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        resp = auth_test_client.get("/test-protected", headers={"Authorization": "Bearer not-a-valid-jwt"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"


def test_protected_route_missing_sub_claim():
    exp = int(time.time()) + 3600
    token = _make_token({"email": "test@example.com", "exp": exp})
    with patch.object(get_settings(), "supabase_jwt_secret", TEST_SECRET):
        resp = auth_test_client.get("/test-protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"


def test_protected_route_unconfigured_secret():
    with patch.object(get_settings(), "supabase_jwt_secret", ""):
        resp = auth_test_client.get("/test-protected", headers={"Authorization": "Bearer some-token"})
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "AUTH_NOT_CONFIGURED"
