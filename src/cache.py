"""In-memory LRU cache to avoid re-scraping identical URLs."""

import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Optional

from .models import ScrapeOptions, ScrapeResult


def _make_cache_key(url: str, options: ScrapeOptions) -> str:
    """Deterministic cache key from URL + relevant scrape options."""
    key_parts = (
        url,
        options.format.value,
        str(options.include_links),
        str(options.include_metadata),
    )
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()


class ScrapeCache:
    """
    Thread-safe in-memory LRU cache for scrape results.

    Each entry has a configurable TTL (default 5 minutes) and the cache
    evicts the least-recently-used item when it reaches capacity.
    """

    def __init__(self, max_size: int = 500, ttl_seconds: int = 300) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[ScrapeResult, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, url: str, options: ScrapeOptions) -> Optional[ScrapeResult]:
        key = _make_cache_key(url, options)
        async with self._lock:
            if key not in self._store:
                return None

            result, inserted_at = self._store[key]
            if time.monotonic() - inserted_at > self._ttl:
                del self._store[key]
                return None

            # Move to end to mark as recently used
            self._store.move_to_end(key)
            cached = result.model_copy(update={"cached": True})
            return cached

    async def set(self, url: str, options: ScrapeOptions, result: ScrapeResult) -> None:
        key = _make_cache_key(url, options)
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (result, time.monotonic())

            # Evict oldest entry if over capacity
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    async def invalidate(self, url: str, options: Optional[ScrapeOptions] = None) -> int:
        """
        Remove one or all cache entries for a URL.
        Returns the number of entries removed.
        """
        async with self._lock:
            if options is not None:
                key = _make_cache_key(url, options)
                if key in self._store:
                    del self._store[key]
                    return 1
                return 0

            # Remove all entries matching the URL prefix (any format/options)
            to_delete = [
                k
                for k, (r, _) in self._store.items()
                if r.url == url or r.url.rstrip("/") == url.rstrip("/")
            ]
            for k in to_delete:
                del self._store[k]
            return len(to_delete)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    @property
    async def size(self) -> int:
        async with self._lock:
            return len(self._store)

    def size_sync(self) -> int:
        """Non-async size check for use in health endpoints."""
        return len(self._store)

    async def purge_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, ts) in self._store.items() if now - ts > self._ttl]
            for k in expired:
                del self._store[k]
            return len(expired)


# Module-level singleton
_cache: Optional[ScrapeCache] = None


def get_cache() -> ScrapeCache:
    global _cache
    if _cache is None:
        _cache = ScrapeCache()
    return _cache
