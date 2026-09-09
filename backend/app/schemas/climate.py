"""
app/schemas/climate.py

Pydantic models for /api/v1/climate endpoint backed by Open-Meteo Climate API (CMIP6).
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class ClimateLocation(BaseModel):
    """Geographic location and resolved place name for climate queries."""

    latitude: float = Field(ge=-90.0, le=90.0, description="Latitude coordinate.")
    longitude: float = Field(ge=-180.0, le=180.0, description="Longitude coordinate.")
    name: Optional[str] = Field(default=None, description="Human-readable location name.")


class ClimatePeriod(BaseModel):
    """Start and end dates of the requested climate period."""

    start: str = Field(description="Start date in YYYY-MM-DD format.")
    end: str = Field(description="End date in YYYY-MM-DD format.")


class ClimateDailyPoint(BaseModel):
    """Daily climate metrics for a single date."""

    date: str = Field(description="Date in YYYY-MM-DD format.")
    temperature_mean: Optional[float] = Field(
        default=None, description="Daily mean temperature in °C."
    )
    temperature_max: Optional[float] = Field(
        default=None, description="Daily maximum temperature in °C."
    )
    temperature_min: Optional[float] = Field(
        default=None, description="Daily minimum temperature in °C."
    )
    precipitation: Optional[float] = Field(
        default=None, description="Daily precipitation sum in mm."
    )
    humidity: Optional[float] = Field(
        default=None, description="Daily mean relative humidity in %."
    )
    wind_speed: Optional[float] = Field(
        default=None, description="Daily mean wind speed in km/h."
    )


class ClimateSummary(BaseModel):
    """Calculated statistical summary across the queried period."""

    average_temperature: Optional[float] = Field(
        default=None, description="Mean temperature across the period in °C."
    )
    total_precipitation: Optional[float] = Field(
        default=None, description="Total precipitation volume across the period in mm."
    )
    average_humidity: Optional[float] = Field(
        default=None, description="Average relative humidity across the period in %."
    )
    average_wind_speed: Optional[float] = Field(
        default=None, description="Average wind speed across the period in km/h."
    )


class ClimateTrend(BaseModel):
    """Qualitative trend over the observed period (e.g. increasing, stable, decreasing)."""

    temperature: Optional[str] = Field(
        default=None, description="Temperature trend direction: increasing, stable, or decreasing."
    )
    precipitation: Optional[str] = Field(
        default=None, description="Precipitation trend direction: increasing, stable, or decreasing."
    )


class ClimateDataSource(BaseModel):
    """Provenance and attribution for climate models."""

    provider: str = Field(default="Open-Meteo", description="Data provider.")
    dataset: str = Field(default="CMIP6", description="Underlying climate model dataset.")
    attribution: str = Field(
        default="Open-Meteo / CMIP6",
        description="Required data attribution notice.",
    )


class ClimateResponse(BaseModel):
    """Response returned by GET /api/v1/climate."""

    location: ClimateLocation
    period: ClimatePeriod
    model: str = Field(description="Climate model identifier (e.g. CMCC_CM2_VHR4, EC_Earth3P_HR).")
    daily: List[ClimateDailyPoint] = Field(
        default_factory=list, description="Day-by-day climate projections."
    )
    summary: ClimateSummary
    trend: Optional[ClimateTrend] = None
    data_source: ClimateDataSource = Field(default_factory=ClimateDataSource)
