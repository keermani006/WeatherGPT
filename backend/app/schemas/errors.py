"""
app/schemas/errors.py

Canonical error response shapes used across all endpoints.
"""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Machine-readable error code plus a human-readable message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Top-level error envelope returned by all endpoints on failure."""

    error: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": {
                        "code": "LOCATION_NOT_FOUND",
                        "message": "The requested location could not be found.",
                    }
                }
            ]
        }
    }
