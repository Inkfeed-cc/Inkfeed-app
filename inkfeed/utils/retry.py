from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

RETRYABLE = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
)

T = TypeVar("T")


def with_retry(
    fn: Callable[..., T],
    *args: object,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: object,
) -> T:
    """Call *fn* with automatic retry on transient network errors.

    Uses exponential backoff: 1s, 2s, 4s, ...
    Retries on timeout, connection, protocol errors and 5xx responses.
    Non-retryable errors (4xx, etc.) are raised immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug(
                    "Retry %d/%d for %s after %.1fs: %s",
                    attempt + 1, max_retries, fn.__name__, delay, exc,
                )
                time.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.debug(
                        "Retry %d/%d for %s after %.1fs: %s (HTTP %d)",
                        attempt + 1, max_retries, fn.__name__, delay,
                        exc, exc.response.status_code,
                    )
                    time.sleep(delay)
                    continue
            raise  # 4xx errors are not retryable
    raise last_exc  # type: ignore[misc]  # all retries exhausted
