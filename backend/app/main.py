"""
app/main.py

FastAPI application entry point for WeatherGPT Phase 1 backend.

Responsibilities:
  - Create and configure the FastAPI app with comprehensive OpenAPI documentation.
  - Register all API routers under /api/v1.
  - Configure CORS based on settings.cors_origins.
  - Expose /health (and /healthz) for liveness/readiness checks.
"""

import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import alerts as alerts_router
from app.api.routes import chat as chat_router
from app.api.routes import climate as climate_router
from app.api.routes import location as location_router
from app.api.routes import weather as weather_router
from app.core.config import get_settings

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
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }
)

logger = logging.getLogger(__name__)
settings = get_settings()


import asyncio
from app.services.alert_service import evaluate_all_alerts

# ---------------------------------------------------------------------------
# Lifespan & Alert Scheduler
# ---------------------------------------------------------------------------

async def _alert_scheduler_loop(interval_minutes: int):
    """Periodic background task that evaluates active alerts."""
    interval_seconds = max(1, interval_minutes * 60)
    logger.info("Alert evaluation scheduler started (interval: %d minutes)", interval_minutes)
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


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("WeatherGPT starting up (version=%s)", settings.app_version)
    scheduler_task = asyncio.create_task(
        _alert_scheduler_loop(settings.alert_check_interval_minutes)
    )
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    logger.info("WeatherGPT shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "**WeatherGPT: Conversational AI for Weather Forecasting, Alerts, and Climate Information**\n\n"
        "SIH Project · Phase 1 Backend API.\n\n"
        "Features:\n"
        "- **Conversational Weather AI** backed by Groq and Open-Meteo\n"
        "- **Current, Forecast & Hourly Weather** via Open-Meteo\n"
        "- **Location Geocoding & Autocomplete Search** via Nominatim / OpenStreetMap\n"
        "- **Meteorological Threshold Alerts** backed by Supabase\n"
        "- **Climate Data Status** query interface\n"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------

allowed_origins = settings.get_cors_origins()
# If origins specified, use them; if empty, allow all for dev convenience
origins_list = allowed_origins if allowed_origins else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API Routers (/api/v1)
# ---------------------------------------------------------------------------

app.include_router(chat_router.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(weather_router.router, prefix="/api/v1", tags=["Weather"])
app.include_router(location_router.router, prefix="/api/v1", tags=["Location"])
app.include_router(alerts_router.router, prefix="/api/v1", tags=["Alerts"])
app.include_router(climate_router.router, prefix="/api/v1", tags=["Climate"])


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns standard healthy status for monitoring and uptime probes.",
)
async def health():
    return {"status": "healthy"}


@app.get(
    "/healthz",
    tags=["Health"],
    summary="Liveness check (legacy)",
    description="Returns 200 OK when the service is running.",
)
async def healthz():
    return {"status": "ok", "version": settings.app_version}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["app"])
