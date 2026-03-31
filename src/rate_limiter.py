"""Per-wallet sliding-window rate limiter."""

import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


class RateLimitExceeded(Exception):
    """Raised when a wallet exceeds its allowed request rate."""

    def __init__(self, wallet: str, retry_after: float) -> None:
        self.wallet = wallet
        self.retry_after = retry_after
        super().__init__(
            f"Wallet {wallet} has exceeded the rate limit. "
            f"Retry after {retry_after:.1f}s."
        )


class SlidingWindowRateLimiter:
    """
    Token-bucket / sliding-window rate limiter keyed by payer wallet address.

    Default: 60 requests per 60-second window (1 req/s average, burst up to 10).
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: float = 60.0,
        burst_limit: int = 10,
        burst_window_seconds: float = 5.0,
    ) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._burst_limit = burst_limit
        self._burst_window = burst_window_seconds
        # wallet -> deque of timestamps
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, wallet: str) -> Tuple[bool, Optional[float]]:
        """
        Check whether `wallet` is within rate limits.

        Returns:
            (allowed, retry_after_seconds)
            If allowed is True, retry_after is None.
        """
        now = time.monotonic()
        async with self._lock:
            window_start = now - self._window
            burst_start = now - self._burst_window

            timestamps = self._requests[wallet]

            # Prune timestamps outside the full window
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()

            # Count burst window requests
            burst_count = sum(1 for t in timestamps if t >= burst_start)

            if len(timestamps) >= self._max_requests:
                # Oldest request determines when the window opens up
                retry_after = self._window - (now - timestamps[0])
                return False, max(retry_after, 0.0)

            if burst_count >= self._burst_limit:
                oldest_burst = next(t for t in timestamps if t >= burst_start)
                retry_after = self._burst_window - (now - oldest_burst)
                return False, max(retry_after, 0.0)

            timestamps.append(now)
            return True, None

    async def enforce(self, wallet: str) -> None:
        """
        Enforce rate limit; raises RateLimitExceeded if the wallet is over limit.
        """
        allowed, retry_after = await self.check(wallet)
        if not allowed:
            raise RateLimitExceeded(wallet, retry_after or 1.0)

    async def reset(self, wallet: str) -> None:
        """Clear all rate-limit history for a wallet (useful in tests)."""
        async with self._lock:
            self._requests.pop(wallet, None)

    async def get_stats(self, wallet: str) -> Dict[str, float]:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._requests.get(wallet, deque())
            window_start = now - self._window
            recent = [t for t in timestamps if t >= window_start]
            return {
                "requests_in_window": len(recent),
                "max_requests": self._max_requests,
                "window_seconds": self._window,
                "remaining": max(0, self._max_requests - len(recent)),
            }


# Module-level singleton
_limiter: Optional[SlidingWindowRateLimiter] = None


def get_rate_limiter() -> SlidingWindowRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowRateLimiter()
    return _limiter
