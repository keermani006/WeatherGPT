"""
app/schemas/chat.py

Pydantic models for request / response validation.
These are the shapes the client interacts with.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────

class HistoryMessage(BaseModel):
    """A single turn in conversation history."""
    role: str = Field(description="'user' or 'assistant'")
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    """Incoming chat message from the user."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="The user's natural-language weather question.",
        examples=["Will it rain tomorrow?"],
    )
    location: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional explicit location override (city, place, etc.).",
        examples=["Chennai"],
    )
    latitude: Optional[float] = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="User GPS latitude provided by browser Geolocation API.",
        examples=[13.0827],
    )
    longitude: Optional[float] = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="User GPS longitude provided by browser Geolocation API.",
        examples=[80.2707],
    )
    history: Optional[List[HistoryMessage]] = Field(
        default=None,
        max_length=20,
        description="Previous conversation turns (most recent last, up to 20 turns).",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "Will it rain here today?", "latitude": 13.0827, "longitude": 80.2707},
                {"message": "What's the weather in Mumbai?"},
                {"message": "Is it cold today?", "location": "Manali"},
                {"message": "Is it safe to travel from Chennai to Delhi now?",
                 "latitude": 13.0827, "longitude": 80.2707},
            ]
        }
    }


# ── Internal weather data (also exposed in the response) ─────────────────────

class WeatherData(BaseModel):
    """Normalised weather data returned to the client."""

    location: str
    temperature: float = Field(description="Temperature in °C")
    feels_like: float = Field(description="Feels-like temperature in °C")
    condition: str = Field(description="Human-readable sky condition, e.g. 'Cloudy'")
    humidity: int = Field(description="Relative humidity (%)")
    wind_speed: float = Field(description="Wind speed in m/s")
    rain_probability: Optional[float] = Field(
        default=None, description="Probability of precipitation (0–100 %)"
    )
    rainfall: Optional[float] = Field(
        default=None, description="Rainfall amount (mm)"
    )
    forecast_date: Optional[str] = Field(
        default=None, description="ISO date string for forecast day (null = current)"
    )


# ── Alert suggestion (returned when LLM detects alert intent) ─────────────────

class AlertSuggestion(BaseModel):
    """A suggested alert the user can create with one click."""
    location_name: str = Field(description="Human-readable location for the alert")
    latitude: float
    longitude: float
    condition: str = Field(description="Alert condition: rain_probability | temperature | wind_speed | precipitation")
    threshold: float = Field(description="Threshold value")
    description: str = Field(description="Short human explanation, e.g. 'Alert when rain > 70% in Delhi'")


# ── Response ─────────────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    """Full response returned to the client."""

    answer: str = Field(description="Natural-language answer from WeatherGPT.")
    location: str = Field(description="Resolved location used for weather lookup.")
    weather_data: Optional[WeatherData] = Field(
        default=None,
        description="Structured weather data backing the answer (null if location unknown).",
    )
    destination_weather: Optional[WeatherData] = Field(
        default=None,
        description="Weather at the destination for travel queries.",
    )
    alert_suggestion: Optional[AlertSuggestion] = Field(
        default=None,
        description="Pre-filled alert params when user expresses alert intent.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "answer": "There is a 70% chance of rain tomorrow in Chennai.",
                    "location": "Chennai",
                    "weather_data": {
                        "location": "Chennai",
                        "temperature": 30.0,
                        "feels_like": 34.0,
                        "condition": "Cloudy",
                        "humidity": 78,
                        "wind_speed": 3.5,
                        "rain_probability": 70.0,
                        "rainfall": None,
                        "forecast_date": "2026-09-09",
                    },
                    "alert_suggestion": None,
                }
            ]
        }
    }


# ── Error response ────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Standard error shape."""

    detail: str
