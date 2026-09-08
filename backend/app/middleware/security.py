"""
app/middleware/security.py

HTTP security headers middleware + per-request ID injection.

Headers added to every response:
  X-Request-ID          : unique UUID4 per request (useful for log correlation)
  X-Content-Type-Options: nosniff
  X-Frame-Options       : DENY
  Referrer-Policy       : strict-origin-when-cross-origin

Intentionally omitted:
  Strict-Transport-Security — only safe when TLS is confirmed; omit for HTTP dev
  Content-Security-Policy   — frontend-specific; configure at reverse-proxy layer

The X-Request-ID is also stored in request.state so route handlers and
middleware can include it in log output for correlation.
"""

import uuid
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects security headers and a unique X-Request-ID into every HTTP response.

    X-Request-ID is stored in request.state.request_id for use in log records.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response: Response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value

        return response
