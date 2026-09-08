"""
app/core/auth.py

Supabase JWT authentication dependency for FastAPI.

Architecture:
  Frontend  → Supabase Auth JS → access_token (JWT, HS256)
  Frontend  → Authorization: Bearer <access_token>
  FastAPI   → verify JWT using SUPABASE_JWT_SECRET
  FastAPI   → extract sub (user UUID) → AuthUser

Security rules:
  - The user_id ALWAYS comes from the verified JWT (never from request body).
  - JWTs are verified using the HS256 secret from Supabase project settings.
  - Expired, missing, malformed, or unsigned tokens are rejected with HTTP 401.
  - The JWT itself is NEVER logged.

Setup:
  SUPABASE_JWT_SECRET = <value from Supabase Dashboard → Project Settings → API → JWT Secret>

If SUPABASE_JWT_SECRET is not configured, protected endpoints return HTTP 503.
"""

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


class AuthUser:
    """Verified identity extracted from a Supabase JWT."""

    __slots__ = ("id", "email", "role")

    def __init__(self, user_id: str, email: str = "", role: str = "authenticated") -> None:
        self.id = user_id
        self.email = email
        self.role = role

    def __repr__(self) -> str:
        return f"AuthUser(id={self.id!r})"


def _decode_jwt(token: str) -> dict:
    """
    Decode and verify a Supabase JWT using the HS256 secret.

    Raises:
        jwt.ExpiredSignatureError  → token has expired
        jwt.InvalidTokenError      → malformed or invalid signature
    """
    settings = get_settings()
    secret = settings.supabase_jwt_secret.strip()
    if not secret:
        raise RuntimeError("SUPABASE_JWT_SECRET is not configured")

    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={"require": ["sub", "exp"]},
    )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> AuthUser:
    """
    FastAPI dependency: verify Bearer token and return AuthUser.

    Usage:
        @router.post("/protected")
        async def handler(user: AuthUser = Depends(get_current_user)):
            ...
    """
    settings = get_settings()

    if not settings.supabase_jwt_secret.strip():
        logger.error("Authentication attempted but SUPABASE_JWT_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "AUTH_NOT_CONFIGURED",
                    "message": "Authentication service is not configured on this server.",
                }
            },
        )

    if credentials is None:
        logger.warning("Request missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail={
                "error": {
                    "code": "MISSING_TOKEN",
                    "message": "Authentication required. Provide a valid Bearer token.",
                }
            },
        )

    try:
        payload = _decode_jwt(credentials.credentials)
    except jwt.ExpiredSignatureError:
        logger.warning("Expired JWT token rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail={
                "error": {
                    "code": "TOKEN_EXPIRED",
                    "message": "Your session has expired. Please sign in again.",
                }
            },
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token rejected: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Invalid authentication token.",
                }
            },
        )

    user_id: str = payload.get("sub", "")
    if not user_id:
        logger.warning("JWT missing 'sub' claim")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Token does not contain a valid user identity.",
                }
            },
        )

    return AuthUser(
        user_id=user_id,
        email=payload.get("email", ""),
        role=payload.get("role", "authenticated"),
    )
