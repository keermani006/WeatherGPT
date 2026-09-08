"""
app/schemas/alert.py

Pydantic models for /api/v1/alerts endpoints.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class AlertCondition(str, Enum):
    """Supported meteorological conditions for alerts."""

    RAIN_PROBABILITY = "rain_probability"
    TEMPERATURE = "temperature"
    WIND_SPEED = "wind_speed"
    PRECIPITATION = "precipitation"


class AlertRequest(BaseModel):
    """Request payload to create a new alert."""

    latitude: float = Field(
        ..., ge=-90.0, le=90.0, description="Latitude for the alert location."
    )
    longitude: float = Field(
        ..., ge=-180.0, le=180.0, description="Longitude for the alert location."
    )
    condition: AlertCondition = Field(
        ...,
        description="Metric to monitor: rain_probability, temperature, wind_speed, precipitation.",
    )
    threshold: float = Field(
        ...,
        description="Threshold value triggering the alert.",
    )
    location_name: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional human-readable name for the alert location.",
    )

    @model_validator(mode="after")
    def validate_threshold(self):
        cond = self.condition
        val = self.threshold
        if cond == AlertCondition.RAIN_PROBABILITY and not (0 <= val <= 100):
            raise ValueError("rain_probability threshold must be between 0 and 100 (percentage).")
        if cond == AlertCondition.WIND_SPEED and val < 0:
            raise ValueError("wind_speed threshold cannot be negative.")
        if cond == AlertCondition.PRECIPITATION and val < 0:
            raise ValueError("precipitation threshold cannot be negative.")
        if cond == AlertCondition.TEMPERATURE and not (-100 <= val <= 70):
            raise ValueError("temperature threshold must be between -100 and 70 °C.")
        return self


class AlertResponse(BaseModel):
    """Schema representing an alert stored in database or memory."""

    id: str = Field(description="Unique alert identifier.")
    latitude: float = Field(description="Latitude for the alert location.")
    longitude: float = Field(description="Longitude for the alert location.")
    condition: str = Field(description="Metric being monitored.")
    threshold: float = Field(description="Trigger threshold value.")
    active: bool = Field(default=True, description="Whether the alert is active.")
    location_name: Optional[str] = Field(
        default=None, description="Human-readable name for the location."
    )
    triggered: bool = Field(
        default=False, description="Whether the threshold is currently crossed."
    )
    current_value: Optional[float] = Field(
        default=None, description="Most recently observed value for the monitored condition."
    )
    evaluated_at: Optional[str] = Field(
        default=None, description="ISO timestamp of the most recent evaluation."
    )
    last_triggered_at: Optional[str] = Field(
        default=None, description="ISO timestamp when the alert first transitioned to triggered."
    )
    created_at: Optional[str] = Field(
        default=None, description="ISO timestamp when the alert was created."
    )


class AlertsListResponse(BaseModel):
    """Response returned by GET /api/v1/alerts."""

    alerts: List[AlertResponse] = Field(
        default_factory=list, description="List of user-configured alerts."
    )


class DeleteAlertResponse(BaseModel):
    """Response returned when an alert is deleted."""

    message: str = Field(default="Alert deleted successfully.")
    id: str = Field(description="ID of the deleted alert.")


class AlertEvaluationItem(BaseModel):
    """Result of evaluating an individual alert."""

    alert_id: str = Field(description="Alert identifier.")
    triggered: bool = Field(description="Whether the threshold was crossed.")
    current_value: Optional[float] = Field(
        default=None, description="Current value obtained from weather observation."
    )
    condition: Optional[str] = Field(
        default=None, description="Monitored condition metric."
    )
    threshold: Optional[float] = Field(
        default=None, description="Configured trigger threshold."
    )
    evaluated_at: Optional[str] = Field(
        default=None, description="ISO timestamp when evaluation occurred."
    )
    error: Optional[str] = Field(
        default=None, description="Error message if evaluation failed for this alert."
    )


class AlertBatchEvaluationResponse(BaseModel):
    """Response returned by POST /api/v1/alerts/evaluate."""

    evaluated: int = Field(description="Total number of active alerts evaluated.")
    triggered: int = Field(description="Number of alerts currently triggered.")
    results: List[AlertEvaluationItem] = Field(
        default_factory=list, description="List of evaluation results per alert."
    )
