"""
tests/test_resilience.py

Unit tests for resilience patterns:
  - async_retry with exponential backoff
  - Retryable vs non-retryable exceptions
  - CircuitBreaker states (CLOSED, OPEN, HALF_OPEN)
  - CircuitBreaker transitions and cooldown
  - CircuitBreaker reset
"""

import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest

from app.core.resilience import (
    CBState,
    CircuitBreaker,
    ServiceUnavailableError,
    async_retry,
)


@pytest.mark.asyncio
async def test_retry_success_first_attempt():
    mock_fn = AsyncMock(return_value="ok")
    res = await async_retry(mock_fn, max_attempts=3, base_delay=0.01)
    assert res == "ok"
    assert mock_fn.call_count == 1


@pytest.mark.asyncio
async def test_retry_success_after_transient_failure():
    mock_fn = AsyncMock(side_effect=[
        httpx.TimeoutException("timed out"),
        "recovered",
    ])
    res = await async_retry(mock_fn, max_attempts=3, base_delay=0.01)
    assert res == "recovered"
    assert mock_fn.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_attempts():
    mock_fn = AsyncMock(side_effect=httpx.ConnectError("cannot connect"))
    with pytest.raises(httpx.ConnectError):
        await async_retry(mock_fn, max_attempts=3, base_delay=0.01)
    assert mock_fn.call_count == 3


@pytest.mark.asyncio
async def test_retry_does_not_retry_client_errors():
    mock_fn = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError):
        await async_retry(mock_fn, max_attempts=3, base_delay=0.01)
    assert mock_fn.call_count == 1


@pytest.mark.asyncio
async def test_circuit_breaker_transitions():
    cb = CircuitBreaker("test-service", threshold=3, window=10.0, cooldown=0.2)
    assert cb.state == CBState.CLOSED

    # Failure 1 and 2: still CLOSED
    for _ in range(2):
        try:
            async with cb:
                raise httpx.TimeoutException("failed")
        except httpx.TimeoutException:
            pass
    assert cb.state == CBState.CLOSED

    # Failure 3: trips to OPEN
    try:
        async with cb:
            raise httpx.TimeoutException("failed")
    except httpx.TimeoutException:
        pass
    assert cb.state == CBState.OPEN

    # Subsequent request is immediately rejected without executing block
    with pytest.raises(ServiceUnavailableError):
        async with cb:
            pytest.fail("Should not execute when breaker is OPEN")

    # Wait for cooldown
    await asyncio.sleep(0.25)

    # Breaker enters HALF_OPEN on next call and successful probe closes it
    async with cb:
        pass  # Successful probe
    assert cb.state == CBState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_reset():
    cb = CircuitBreaker("reset-service", threshold=1, window=10.0, cooldown=10.0)
    try:
        async with cb:
            raise httpx.TimeoutException("failed")
    except httpx.TimeoutException:
        pass
    assert cb.state == CBState.OPEN

    cb.reset()
    assert cb.state == CBState.CLOSED
