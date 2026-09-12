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
        max_length=2000,
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
    conversation_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Stable conversation ID for multi-turn context continuity. If None, a new conversation is created for authenticated users.",
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


# ── Route Waypoints & Travel Weather Card ────────────────────────────────────

class RouteWaypoint(BaseModel):
    """An intermediate pass-by location along a travel route with its weather."""
    name: str = Field(description="Name of the waypoint / transit city")
    latitude: float
    longitude: float
    distance_km: Optional[float] = Field(default=None, description="Distance from origin in kilometers")
    weather: WeatherData = Field(description="Weather conditions at this waypoint")


class TravelCardData(BaseModel):
    """Structured data for Travel Weather Card returned on route/transit queries."""
    origin: str = Field(description="Origin city or location")
    destination: str = Field(description="Destination city or location")
    overall_risk: str = Field(description="Low | Moderate | High")
    risk_summary: str = Field(description="Concise rationale for the overall risk level")
    route_weather_summary: str = Field(description="Summary of weather along key sections")
    departure_timing: Optional[str] = Field(default=None, description="User departure time/date if specified")
    timing_note: str = Field(description="Timing evaluation or disclaimer when departure time is not provided")
    cargo: Optional[str] = Field(default=None, description="Identified cargo or goods being moved (e.g., Harvested Rice)")
    cargo_risk_advice: Optional[str] = Field(default=None, description="Cargo-specific risk and protection instructions")
    recommendation: str = Field(description="Practical travel recommendation based on actual route forecast")
    waypoints: List[RouteWaypoint] = Field(default_factory=list, description="Waypoints with weather conditions")


# ── Response ─────────────────────────────────────────────────────────────────

# ── Conversation Models ───────────────────────────────────────────────────────

class ConversationMessage(BaseModel):
    """A single stored message in a conversation."""
    id: str
    conversation_id: str
    role: str  # 'user' | 'assistant'
    content: str
    created_at: Optional[str] = None


class ConversationSummary(BaseModel):
    """Metadata for a conversation including its persistent summary."""
    id: str
    user_id: str
    title: Optional[str] = None
    summary: str = ""
    summary_updated_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StructuredConversationState(BaseModel):
    """
    Lightweight structured conversation state maintained per user/conversation.
    Enables instant contextual continuity without relying on regex keyword lists or extra LLM calls.
    """
    user_id: Optional[str] = Field(default=None, description="Owner user ID for strict isolation")
    active_location: Optional[str] = Field(default=None, description="Current primary location")
    origin: Optional[str] = Field(default=None, description="Starting point for travel/route")
    destination: Optional[str] = Field(default=None, description="Destination for travel/route")
    activity: Optional[str] = Field(default=None, description="Activity context: travel, farming, outdoor event, etc.")
    cargo: Optional[str] = Field(default=None, description="Transported cargo or crop")
    date_time: Optional[str] = Field(default=None, description="Temporal reference: now, tomorrow, evening, etc.")
    current_weather_concern: Optional[str] = Field(default=None, description="Primary weather concern: rain, fog, heat, wind, etc.")
    previous_recommendation: Optional[str] = Field(default=None, description="Summary of previous recommendation/risk")
    updated_at: Optional[str] = Field(default=None, description="ISO timestamp of last update")


class ConversationDetail(BaseModel):
    """Full conversation detail: summary + recent messages + structured state."""
    conversation: ConversationSummary
    recent_messages: List[HistoryMessage] = Field(default_factory=list)
    state: Optional[StructuredConversationState] = None


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
    route_waypoints: Optional[List[RouteWaypoint]] = Field(
        default=None,
        description="Intermediate waypoints with weather conditions along the travel route.",
    )
    travel_card: Optional[TravelCardData] = Field(
        default=None,
        description="Dedicated Travel Weather Card data for travel/route queries.",
    )
    alert_suggestion: Optional[AlertSuggestion] = Field(
        default=None,
        description="Pre-filled alert params when user expresses alert intent.",
    )
    created_alert: Optional[dict] = Field(
        default=None,
        description="The alert that was automatically created if user is authenticated.",
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="Stable conversation ID for this multi-turn session.",
    )
    conversation_state: Optional[StructuredConversationState] = Field(
        default=None,
        description="Current structured conversation state.",
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
