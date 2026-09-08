"""
app/schemas/weather.py

Pydantic response models for /api/v1/weather/* endpoints.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class LocationInfo(BaseModel):
    """Location metadata for weather responses."""

    name: str = Field(description="Display name of the location.")
    latitude: float = Field(ge=-90.0, le=90.0, description="Latitude coordinate.")
    longitude: float = Field(ge=-180.0, le=180.0, description="Longitude coordinate.")


class CurrentConditions(BaseModel):
    """Normalized current atmospheric conditions."""

    temperature: float = Field(description="Temperature in °C.")
    feels_like: float = Field(description="Apparent / feels-like temperature in °C.")
    humidity: int = Field(description="Relative humidity percentage (0-100).")
    wind_speed: float = Field(description="Wind speed in km/h.")
    precipitation: float = Field(default=0.0, description="Precipitation in mm.")
    rain_probability: Optional[int] = Field(
        default=None, description="Probability of precipitation (0-100%)."
    )
    condition: str = Field(description="Human-readable weather condition text.")


class CurrentWeatherResponse(BaseModel):
    """Response returned by GET /api/v1/weather/current."""

    location: LocationInfo
    current: CurrentConditions
    updated_at: str = Field(description="ISO timestamp of weather observation.")


class DayForecast(BaseModel):
    """Daily summary forecast entry."""

    date: str = Field(description="Date string in YYYY-MM-DD format.")
    temperature_max: float = Field(description="Maximum daily temperature in °C.")
    temperature_min: float = Field(description="Minimum daily temperature in °C.")
    rain_probability: Optional[int] = Field(
        default=None, description="Precipitation probability percentage (0-100%)."
    )
    precipitation: float = Field(default=0.0, description="Total precipitation in mm.")
    wind_speed: float = Field(description="Maximum wind speed in km/h.")
    condition: str = Field(description="Human-readable weather condition text.")


class ForecastResponse(BaseModel):
    """Response returned by GET /api/v1/weather/forecast."""

    location: LocationInfo
    forecast: List[DayForecast]


class HourlyEntry(BaseModel):
    """Single hour weather entry."""

    time: str = Field(description="Hour string, e.g. '09:00'.")
    temperature: float = Field(description="Temperature in °C.")
    rain_probability: Optional[int] = Field(
        default=None, description="Precipitation probability percentage (0-100%)."
    )
    precipitation: float = Field(default=0.0, description="Precipitation in mm.")
    wind_speed: float = Field(description="Wind speed in km/h.")


class HourlyResponse(BaseModel):
    """Response returned by GET /api/v1/weather/hourly."""

    location: LocationInfo
    date: str = Field(description="Date string in YYYY-MM-DD format.")
    hourly: List[HourlyEntry]
