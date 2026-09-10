"""
app/core/limiter.py

In-memory rate limiter using slowapi (wraps the 'limits' library).
No Redis or external infrastructure required — suitable for single-instance SIH deployment.

IMPORTANT: In-memory limits are per-process. For horizontally scaled deployments,
switch to a Redis-backed storage backend:
  from slowapi.util import get_remote_address
  from limits.storage import RedisStorage
  limiter = Limiter(key_func=get_remote_address, storage_uri="redis://...")

Rate limits are configured via environment variables:
  RATE_LIMIT_CHAT=20/minute
  RATE_LIMIT_LOCATION=30/minute
  RATE_LIMIT_ALERTS=20/minute

Format: "<count>/<period>"  e.g. "20/minute", "5/second", "100/hour"
"""

import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

# Global limiter instance — disabled for now per user request
limiter = Limiter(key_func=get_remote_address, enabled=False)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Return HTTP 429 with the standard WeatherGPT error envelope on rate limit violation.
    Logs the event for observability.
    """
    logger.warning(
        "Rate limit exceeded | path=%s | client=%s | limit=%s",
        request.url.path,
        get_remote_address(request),
        exc.detail,
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": (
                        "Too many requests. Please slow down and try again shortly. "
                        f"Limit: {exc.detail}"
                    ),
                }
            }
        },
        headers={"Retry-After": "60"},
    )
