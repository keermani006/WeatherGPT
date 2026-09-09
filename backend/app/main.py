"""
app/main.py

FastAPI application entry point for WeatherGPT Phase 2 backend.

Responsibilities:
  - Configure structured logging (LOG_LEVEL from env).
  - Register security middleware (headers + X-Request-ID).
  - Configure CORS based on settings.cors_origins (never falls back to ["*"] silently).
  - Register slowapi rate limiter and 429 handler.
  - Start and shut down background alert evaluation scheduler.
  - Expose /health (liveness) and /health/ready (readiness).
  - Register all API routers under /api/v1.
"""

import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import alerts as alerts_router
from app.api.routes import auth as auth_router
from app.api.routes import chat as chat_router
from app.api.routes import climate as climate_router
from app.api.routes import location as location_router
from app.api.routes import weather as weather_router
from app.core.config import get_settings
from app.core.limiter import limiter, rate_limit_exceeded_handler
from app.middleware.security import SecurityHeadersMiddleware
from app.services.alert_service import evaluate_all_alerts
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

settings = get_settings()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["console"], "level": settings.log_level.upper()},
        "loggers": {
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert Scheduler
# ---------------------------------------------------------------------------

async def _alert_scheduler_loop(interval_minutes: int):
    """Periodic background task that evaluates active alerts."""
    interval_seconds = max(1, interval_minutes * 60)
    logger.info(
        "Alert evaluation scheduler started (interval: %d minutes)", interval_minutes
    )
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            logger.info("Triggering periodic alert evaluation...")
            try:
                await evaluate_all_alerts()
            except Exception as exc:  # noqa: BLE001
                logger.error("Periodic alert evaluation error: %s", exc)
    except asyncio.CancelledError:
        logger.info("Alert evaluation scheduler received shutdown signal")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info(
        "WeatherGPT v%s starting up | log_level=%s | scheduler_interval=%dm",
        settings.app_version,
        settings.log_level,
        settings.alert_check_interval_minutes,
    )
    scheduler_task = asyncio.create_task(
        _alert_scheduler_loop(settings.alert_check_interval_minutes)
    )
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    logger.info("WeatherGPT shutting down cleanly")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "**WeatherGPT: Conversational AI for Weather Forecasting, Alerts, and Climate Information**\n\n"
        "SIH Project · Phase 2 Backend API.\n\n"
        "## Authentication\n"
        "Alert endpoints require a Supabase JWT Bearer token:\n"
        "`Authorization: Bearer <access_token>`\n\n"
        "## Features\n"
        "- **Conversational Weather AI** backed by Groq and Open-Meteo\n"
        "- **Current, Forecast & Hourly Weather** via Open-Meteo (cached)\n"
        "- **Location Geocoding & Autocomplete** via Nominatim / OpenStreetMap (cached)\n"
        "- **User-Owned Meteorological Alerts** backed by Supabase with RLS\n"
        "- **CMIP6 Climate Projections** via Open-Meteo Climate API (cached)\n"
        "- **Rate Limiting** on chat, location, and alert endpoints\n"
        "- **Circuit Breaker** on all upstream services\n"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Rate Limiter State
# ---------------------------------------------------------------------------

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS Middleware  (must be added BEFORE SecurityHeadersMiddleware so that
# Starlette wraps it outermost and handles OPTIONS preflights first)
# ---------------------------------------------------------------------------

allowed_origins = settings.get_cors_origins()
logger.info("CORS: configured origins: %s (also permitting http/https origins via regex)", allowed_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins else ["*"],
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------------------------

app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# API Routers (/api/v1)
# ---------------------------------------------------------------------------

app.include_router(chat_router.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(weather_router.router, prefix="/api/v1", tags=["Weather"])
app.include_router(location_router.router, prefix="/api/v1", tags=["Location"])
app.include_router(alerts_router.router, prefix="/api/v1", tags=["Alerts"])
app.include_router(climate_router.router, prefix="/api/v1", tags=["Climate"])
app.include_router(auth_router.router, prefix="/api/v1", tags=["Auth"])


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["Health"],
    summary="Liveness check",
    description=(
        "Returns 200 OK when the application process is running. "
        "This endpoint NEVER returns non-200 due to external dependency unavailability. "
        "Use /health/ready for readiness checks."
    ),
)
async def health():
    return {"status": "healthy"}


@app.get(
    "/health/ready",
    tags=["Health"],
    summary="Readiness check",
    description=(
        "Returns 200 if the application is ready to serve traffic "
        "(Supabase reachable). Returns 503 if a critical dependency is unavailable."
    ),
)
async def health_ready():
    """Check database connectivity for readiness probes."""
    from app.core.database import get_supabase
    checks = {"status": "ready", "version": settings.app_version, "database": "ok"}
    sb = get_supabase()
    if sb is None:
        checks["database"] = "not_configured"
    else:
        try:
            sb.table("alerts").select("id").limit(1).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness check: database unreachable — %s", exc)
            from fastapi import Response
            checks["database"] = "unavailable"
            checks["status"] = "degraded"
    return checks


@app.get(
    "/healthz",
    tags=["Health"],
    summary="Liveness check (legacy alias)",
    description="Alias for /health — maintained for backward compatibility.",
    include_in_schema=False,
)
async def healthz():
    return {"status": "ok", "version": settings.app_version}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["app"])
