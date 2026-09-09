"""
app/core/database.py

Supabase client singleton.

Usage:
    from app.core.database import get_supabase
    sb = get_supabase()
    data = sb.table("alerts").select("*").execute()

The client is created once and reused across the application.
If SUPABASE_URL / SUPABASE_ANON_KEY are empty (e.g. running tests without
Supabase), the module returns None and the alert service falls back to an
in-memory store so the app still starts cleanly.
"""

import logging
from functools import lru_cache
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase():
    """Return a cached Supabase client, or None if credentials are not set."""
    settings = get_settings()

    auth_key = (
        settings.supabase_secret_key
        or settings.supabase_service_role_key
        or settings.supabase_anon_key
    ).strip()

    if not settings.supabase_url or not auth_key:
        logger.warning(
            "SUPABASE_URL or Supabase key not set — "
            "alerts will use in-memory fallback storage."
        )
        return None

    try:
        from supabase import create_client  # imported lazily
        raw_url = settings.supabase_url.strip()
        # Supabase Python SDK expects the project root URL, not the /rest/v1/ endpoint
        clean_url = raw_url.split("/rest/v1")[0].rstrip("/") if "/rest/v1" in raw_url else raw_url.rstrip("/")
        client = create_client(clean_url, auth_key)
        key_type = "secret/service_role" if (settings.supabase_secret_key or settings.supabase_service_role_key) else "anon"
        logger.info("Supabase client initialised (url=%s, auth=%s)", clean_url, key_type)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialise Supabase client: %s", exc)
        return None
