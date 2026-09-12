"""
app/core/redis.py

Centralized Redis Cloud client connection manager.
Reuses a single connection pool across rate limiting, user location caching,
and multi-instance shared state.
"""

import logging
from typing import Optional
import redis.asyncio as aioredis
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


def get_redis_client() -> Optional[aioredis.Redis]:
    """
    Get or initialize the shared async Redis client singleton.
    Returns None if REDIS_URL is not configured or connection initialization fails.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    settings = get_settings()
    url = (settings.redis_url or "").strip()
    if not url:
        logger.debug("No REDIS_URL configured; Redis features disabled/degraded")
        return None

    try:
        # Create connection pool with sensible timeouts
        _redis_client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        logger.info("Initialized shared Redis client from REDIS_URL")
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialize Redis client: %s", exc)
        _redis_client = None
        return None


async def close_redis_client() -> None:
    """Close the shared Redis client connection pool on app shutdown."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Closed Redis connection pool")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error closing Redis client: %s", exc)
        finally:
            _redis_client = None


async def ping_redis() -> bool:
    """Check if the Redis Cloud instance is reachable and responsive."""
    client = get_redis_client()
    if client is None:
        return False
    try:
        res = await client.ping()
        return bool(res)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis ping failed: %s", exc)
        return False
