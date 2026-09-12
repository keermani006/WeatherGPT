"""
app/core/limiter.py

Centralized Redis Cloud rate limiter with per-user isolation.
Enforces atomic rate limits using Redis Lua scripts.
Rate-limit identity is extracted strictly from verified JWT `sub` for authenticated
users and falls back to client IP for unauthenticated requests.
"""

import functools
import logging
from typing import Any, Callable, Optional, Tuple

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

# Atomic Redis Lua script for sliding/fixed-window rate limiting
# Increments counter and ensures TTL is set atomically on the key.
RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
local ttl = redis.call('TTL', key)
if ttl == -1 then
    redis.call('EXPIRE', key, window)
    ttl = window
end
return {current, ttl}
"""


class RateLimitExceeded(HTTPException):
    """Exception raised when an identity exceeds their rate limit."""

    def __init__(self, detail: str = "Rate limit exceeded", retry_after: int = 60) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


def parse_rate_limit(limit_str: str) -> Tuple[int, int, str]:
    """
    Parse a rate-limit string such as "120/minute", "5/second", "100/hour".
    Returns (limit_count, window_seconds, window_name).
    """
    if not limit_str or "/" not in limit_str:
        return 120, 60, "minute"
    parts = limit_str.split("/", 1)
    try:
        count = int(parts[0].strip())
    except ValueError:
        count = 120

    period = parts[1].strip().lower()
    if "sec" in period:
        return count, 1, "second"
    elif "min" in period:
        return count, 60, "minute"
    elif "hour" in period:
        return count, 3600, "hour"
    elif "day" in period:
        return count, 86400, "day"
    return count, 60, "minute"


def extract_rate_limit_identity(request: Request) -> Tuple[str, bool]:
    """
    Extract rate-limiting identity.
    Returns (identity_string, is_authenticated).

    SECURITY REQUIREMENTS:
      - Authenticated user identity MUST come from verified JWT `sub`.
      - Never trusts user_id from query params, request body, or client headers.
      - Never uses the raw JWT string as the key.
      - Unauthenticated fallback uses client IP.
    """
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            try:
                from app.core.auth import _decode_jwt
                payload = _decode_jwt(token)
                user_id = str(payload.get("sub", "")).strip()
                if user_id:
                    return user_id, True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to extract authenticated user from JWT for rate limiting: %s", exc)

    # Fallback to client IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host
    else:
        client_ip = "127.0.0.1"

    if client_ip in ("::1", "localhost"):
        client_ip = "127.0.0.1"

    return f"ip_{client_ip}", False


class RedisRateLimiter:
    """
    Centralized Redis-backed rate limiter with per-user isolation.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def reset(self) -> None:
        """Reset helper (used in testing)."""
        pass

    async def check_rate_limit(
        self,
        request: Request,
        endpoint_name: str,
        limit_str: str,
    ) -> None:
        """
        Check rate limit in Redis Cloud for the request identity.
        Raises RateLimitExceeded (HTTP 429) if quota is exhausted.
        """
        if not self.enabled:
            return

        limit, window, window_name = parse_rate_limit(limit_str)
        identity, is_auth = extract_rate_limit_identity(request)

        # Redis key format: rate_limit:{identity}:{endpoint}:{window}
        redis_key = f"rate_limit:{identity}:{endpoint_name}:{window_name}"

        redis = get_redis_client()
        if redis is None:
            # Documented failure behavior: fail-open with warning if Redis is unavailable
            logger.warning(
                "Redis client unavailable for rate limiting | identity=%s | key=%s",
                identity,
                redis_key,
            )
            return

        try:
            res = await redis.eval(RATE_LIMIT_LUA, 1, redis_key, limit, window)
            current, ttl = res[0], res[1]
            if current > limit:
                logger.warning(
                    "Rate limit exceeded | identity=%s | key=%s | count=%d/%d | ttl=%ds",
                    identity,
                    redis_key,
                    current,
                    limit,
                    ttl,
                )
                raise RateLimitExceeded(
                    detail=f"Rate limit exceeded for {endpoint_name}. Try again in {max(1, ttl)}s.",
                    retry_after=max(1, ttl),
                )
        except RateLimitExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            # Redis failure resilience: log and allow request to prevent full outage
            logger.error("Redis rate limiter evaluation failed (%s); failing open", exc)
            return

    def limit(self, limit_str: str, endpoint: Optional[str] = None) -> Callable:
        """
        Decorator for FastAPI endpoints.
        Usage:
            @limiter.limit(settings.rate_limit_chat)
            async def chat(request: Request, ...):
        """
        def decorator(func: Callable) -> Callable:
            ep_name = endpoint or func.__name__.replace("_endpoint", "")

            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                request: Optional[Request] = kwargs.get("request")
                if request is None:
                    for arg in args:
                        if isinstance(arg, Request):
                            request = arg
                            break

                if request is not None:
                    await self.check_rate_limit(request, ep_name, limit_str)

                return await func(*args, **kwargs)

            return wrapper

        return decorator


# Global singleton instance
limiter = RedisRateLimiter(enabled=True)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Return HTTP 429 with standard WeatherGPT error envelope on rate limit violation.
    """
    retry_after = getattr(exc, "retry_after", 10)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": (
                        "Too many requests. Please slow down and try again shortly. "
                        f"{exc.detail}"
                    ),
                }
            }
        },
        headers={"Retry-After": str(retry_after)},
    )
