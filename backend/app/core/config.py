"""
app/core/config.py

Central configuration using Pydantic Settings.
All secrets and tunable parameters come from the environment / .env file.
"""

from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ─────────────────────────────────────────────────────────────────
    app_name: str = "WeatherGPT"
    app_version: str = "2.0.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── Groq LLM ────────────────────────────────────────────────────────────
    groq_api_key: str
    groq_model: str = "qwen/qwen3.8-27b"
    max_message_length: int = 2000
    llm_max_output_tokens: int = 800

    # ── Open-Meteo Weather API ──────────────────────────────────────────────
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    open_meteo_climate_url: str = "https://climate-api.open-meteo.com/v1"

    # ── Nominatim / OpenStreetMap Geocoding ──────────────────────────────────
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "WeatherGPT/2.0 (sih-weathergpt-backend)"

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_secret_key: str = ""
    # JWT Secret from Supabase Project Settings → API → JWT Secret
    supabase_jwt_secret: str = ""

    # ── Alerts Scheduler ───────────────────────────────────────────────────
    alert_check_interval_minutes: int = 15

    # ── CORS ────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g.:
    #   CORS_ORIGINS=http://localhost:3000,http://localhost:5173
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:8080"

    # ── HTTP timeouts (seconds) ─────────────────────────────────────────────
    weather_api_timeout: int = 10
    llm_timeout: int = 30
    geo_timeout: int = 5

    # ── Redis Cloud ─────────────────────────────────────────────────────────
    redis_url: str = ""

    # ── Rate Limiting (Redis-backed, per-user) ──────────────────────────────
    # Format: "<count>/<period>" — e.g. "120/minute", "100/hour"
    rate_limit_chat: str = "120/minute"
    rate_limit_location: str = "120/minute"
    rate_limit_alerts: str = "120/minute"

    # ── Cache TTLs (seconds) ────────────────────────────────────────────────
    weather_cache_ttl: int = 300      # 5 minutes
    forecast_cache_ttl: int = 600     # 10 minutes
    hourly_cache_ttl: int = 300       # 5 minutes
    climate_cache_ttl: int = 3600     # 1 hour (climate data changes infrequently)
    location_cache_ttl: int = 3600    # 1 hour
    recent_locations_ttl: int = 86400 * 30  # 30 days
    recent_locations_max: int = 10

    # ── Conversational Memory ────────────────────────────────────────────────
    recent_message_limit: int = 10          # sliding window size (messages)
    summary_trigger_threshold: int = 12     # messages before rolling summary
    max_context_tokens: int = 4000          # max token budget for context
    conversation_cache_ttl: int = 3600      # Redis TTL for conversation cache (1 hour)

    # ── Resilience ──────────────────────────────────────────────────────────
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.5    # seconds; doubles each attempt
    circuit_breaker_threshold: int = 5
    circuit_breaker_window: int = 60   # seconds
    circuit_breaker_cooldown: int = 30  # seconds

    def get_cors_origins(self) -> List[str]:
        """Parse CORS_ORIGINS env var into a list of origin strings."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
