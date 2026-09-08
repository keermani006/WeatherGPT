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
    app_version: str = "1.0.0"
    debug: bool = False

    # ── Groq LLM ────────────────────────────────────────────────────────────
    groq_api_key: str
    groq_model: str = "qwen/qwen3.8-27b"

    # ── Open-Meteo Weather API ──────────────────────────────────────────────
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    open_meteo_climate_url: str = "https://climate-api.open-meteo.com/v1"

    # ── Nominatim / OpenStreetMap Geocoding ──────────────────────────────────
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "WeatherGPT/1.0 (sih-weathergpt-backend)"

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_secret_key: str = ""

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

    def get_cors_origins(self) -> List[str]:
        """Parse CORS_ORIGINS env var into a list of origin strings."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
