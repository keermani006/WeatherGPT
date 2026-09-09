"""
app/core/resilience.py

Lightweight retry-with-backoff and circuit breaker for upstream HTTP services.

No external infrastructure required — purely in-process state.

Circuit Breaker States:
  CLOSED  → normal operation; failures counted
  OPEN    → upstream hammering stopped; requests rejected immediately
  HALF_OPEN → one probe request allowed; if it succeeds → CLOSED, else → OPEN

Limitations (acceptable for SIH single-instance demo):
  - State is in-memory; resets on server restart.
  - Per-process only; not shared across instances.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Optional, Tuple, Type

import httpx

logger = logging.getLogger(__name__)

# Exceptions that indicate a transient upstream failure worth retrying.
_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


async def async_retry(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    **kwargs,
):
    """
    Retry an async function with exponential backoff on transient failures.

    Retries on:
      - httpx.TimeoutException
      - httpx.ConnectError
      - httpx.RemoteProtocolError
      - HTTP 5xx responses (httpx.HTTPStatusError with status >= 500)

    Never retries:
      - HTTP 4xx (client errors — retrying won't help)
      - Other exceptions (unexpected; surface immediately)

    Delays: base_delay → base_delay*2 → base_delay*4 ...
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                # 4xx — client error, no point retrying
                raise
            last_exc = exc
            logger.warning(
                "Upstream HTTP %d on attempt %d/%d — %s",
                exc.response.status_code, attempt, max_attempts, fn.__name__,
            )
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            logger.warning(
                "Transient upstream error on attempt %d/%d — %s: %s",
                attempt, max_attempts, fn.__name__, exc,
            )
        except Exception:
            raise  # Unexpected — surface immediately

        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            logger.debug("Retry backoff: sleeping %.1fs before attempt %d", delay, attempt + 1)
            await asyncio.sleep(delay)

    # All attempts exhausted
    logger.error("All %d attempts failed for %s", max_attempts, fn.__name__)
    raise last_exc  # type: ignore[misc]


class CBState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ServiceUnavailableError(RuntimeError):
    """Raised by CircuitBreaker when the upstream is OPEN (circuit tripped)."""


class CircuitBreaker:
    """
    Per-upstream circuit breaker.

    Usage:
        _cb = CircuitBreaker("open-meteo", threshold=5, window=60, cooldown=30)

        async def call_upstream():
            async with _cb:
                return await httpx_client.get(...)
    """

    def __init__(
        self,
        name: str,
        threshold: int = 5,
        window: int = 60,
        cooldown: int = 30,
    ) -> None:
        self.name = name
        self._threshold = threshold
        self._window = window       # seconds — sliding window for failure counting
        self._cooldown = cooldown   # seconds — OPEN → HALF_OPEN wait

        self._state = CBState.CLOSED
        self._failure_times: list[float] = []
        self._opened_at: Optional[float] = None

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def state(self) -> CBState:
        return self._state

    async def __aenter__(self):
        self._check_state()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._on_success()
        elif issubclass(exc_type, (ServiceUnavailableError,)):
            pass  # Already handled
        elif issubclass(exc_type, _RETRYABLE_EXCEPTIONS):
            self._on_failure()
        elif issubclass(exc_type, httpx.HTTPStatusError):
            if exc_val.response.status_code >= 500:
                self._on_failure()
        return False  # Do not suppress exceptions

    # ── State machine ─────────────────────────────────────────────────────────

    def _check_state(self) -> None:
        now = time.monotonic()
        if self._state == CBState.CLOSED:
            return
        if self._state == CBState.OPEN:
            if self._opened_at and (now - self._opened_at) >= self._cooldown:
                logger.info(
                    "Circuit breaker [%s]: OPEN → HALF_OPEN (cooldown elapsed)", self.name
                )
                self._state = CBState.HALF_OPEN
                return
            raise ServiceUnavailableError(
                f"Circuit breaker open for upstream '{self.name}'. "
                "Service temporarily unavailable."
            )
        # HALF_OPEN — allow one probe through

    def _on_success(self) -> None:
        if self._state in (CBState.HALF_OPEN, CBState.OPEN):
            logger.info(
                "Circuit breaker [%s]: %s → CLOSED (probe succeeded)", self.name, self._state
            )
        self._state = CBState.CLOSED
        self._failure_times.clear()
        self._opened_at = None

    def _on_failure(self) -> None:
        now = time.monotonic()
        # Prune failures outside the sliding window
        self._failure_times = [t for t in self._failure_times if now - t < self._window]
        self._failure_times.append(now)

        if self._state == CBState.HALF_OPEN:
            logger.warning(
                "Circuit breaker [%s]: HALF_OPEN → OPEN (probe failed)", self.name
            )
            self._state = CBState.OPEN
            self._opened_at = now
            return

        if len(self._failure_times) >= self._threshold:
            logger.error(
                "Circuit breaker [%s]: CLOSED → OPEN (%d failures in %ds window)",
                self.name, len(self._failure_times), self._window,
            )
            self._state = CBState.OPEN
            self._opened_at = now

    def reset(self) -> None:
        """Force-reset to CLOSED (useful in tests)."""
        self._state = CBState.CLOSED
        self._failure_times.clear()
        self._opened_at = None


# ── Module-level circuit breaker singletons ───────────────────────────────────
# One per upstream service; shared across all requests in the process.

from app.core.config import get_settings as _get_settings  # noqa: E402


def _make_cb(name: str) -> CircuitBreaker:
    s = _get_settings()
    return CircuitBreaker(
        name,
        threshold=s.circuit_breaker_threshold,
        window=s.circuit_breaker_window,
        cooldown=s.circuit_breaker_cooldown,
    )


cb_open_meteo = _make_cb("open-meteo")
cb_climate = _make_cb("open-meteo-climate")
cb_nominatim = _make_cb("nominatim")
cb_groq = _make_cb("groq")
