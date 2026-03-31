"""Web Scraping Gateway — pay-per-page scraping API powered by Mainlayer.

Agents send a payer wallet address via the X-Payer-Wallet header.
The gateway verifies payment with Mainlayer ($0.001 per page) and returns
clean text, HTML, or Markdown of the requested URL.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cache import ScrapeCache, get_cache
from .mainlayer import MainlayerClient, MainlayerError, get_mainlayer_client
from .models import (
    HealthResponse,
    PaymentRequiredResponse,
    PricingResponse,
    ScrapeRequest,
    ScrapeResult,
)
from .rate_limiter import RateLimitExceeded, SlidingWindowRateLimiter, get_rate_limiter
from .scraper import ScraperError, scrape

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RESOURCE_ID: str = os.getenv("RESOURCE_ID", "web-scraping-gateway-v1")
PRICE_USD: float = 0.001
MAINLAYER_PAY_ENDPOINT: str = "https://api.mainlayer.xyz/pay"

_start_time = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Attach singletons to app state for dependency injection in tests
    app.state.mainlayer_client = get_mainlayer_client()
    app.state.cache = get_cache()
    app.state.rate_limiter = get_rate_limiter()
    logger.info("Web Scraping Gateway started. Resource ID: %s", RESOURCE_ID)
    yield
    await app.state.mainlayer_client.aclose()
    logger.info("Web Scraping Gateway shutting down.")


app = FastAPI(
    title="Web Scraping Gateway",
    description=(
        "Pay-per-page web scraping for AI agents. "
        "Pay $0.001 per page via Mainlayer — no API key registration required."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def _get_mainlayer(request: Request) -> MainlayerClient:
    return getattr(request.app.state, "mainlayer_client", get_mainlayer_client())


def _get_cache(request: Request) -> ScrapeCache:
    return getattr(request.app.state, "cache", get_cache())


def _get_limiter(request: Request) -> SlidingWindowRateLimiter:
    return getattr(request.app.state, "rate_limiter", get_rate_limiter())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "Web Scraping Gateway",
        "version": "1.0.0",
        "description": "Pay-per-page web scraping for AI agents",
        "endpoints": {
            "scrape": "POST /scrape",
            "pricing": "GET /pricing",
            "health": "GET /health",
            "docs": "GET /docs",
        },
    }


@app.get("/health", response_model=HealthResponse, tags=["Meta"])
async def health(request: Request) -> HealthResponse:
    """Service health check."""
    cache = _get_cache(request)
    return HealthResponse(
        status="ok",
        version="1.0.0",
        cache_entries=cache.size_sync(),
        uptime_seconds=round(time.monotonic() - _start_time, 2),
    )


@app.get("/pricing", response_model=PricingResponse, tags=["Meta"])
async def get_pricing() -> PricingResponse:
    """
    Return pricing information for the scraping gateway.

    Agents should inspect this endpoint before constructing a payment.
    """
    return PricingResponse(
        price_per_page_usd=PRICE_USD,
        resource_id=RESOURCE_ID,
        supported_formats=["text", "html", "markdown"],
        description="Pay-per-page web scraping. No API key registration required.",
        pay_endpoint=MAINLAYER_PAY_ENDPOINT,
        docs_url="https://api.mainlayer.xyz/docs",
    )


@app.post(
    "/scrape",
    response_model=ScrapeResult,
    status_code=200,
    tags=["Scraping"],
    responses={
        402: {"description": "Payment required", "model": PaymentRequiredResponse},
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
        502: {"description": "Upstream scraping error"},
    },
)
async def scrape_url(
    scrape_request: ScrapeRequest,
    request: Request,
    x_payer_wallet: str = Header(
        default=None,
        description="Mainlayer payer wallet address. Payment of $0.001 must be made before calling this endpoint.",
    ),
) -> ScrapeResult | JSONResponse:
    """
    Scrape a URL and return its content.

    **Cost**: $0.001 per page

    Provide your Mainlayer payer wallet address in the `X-Payer-Wallet` header.
    The gateway verifies your payment and returns the page in the requested format
    (text, HTML, or Markdown).

    No API key registration needed — just pay via Mainlayer and scrape.
    """
    mainlayer = _get_mainlayer(request)
    cache = _get_cache(request)
    limiter = _get_limiter(request)

    url_str = str(scrape_request.url)

    # --- 1. Payment check ---
    if not x_payer_wallet:
        logger.info("Rejected: missing X-Payer-Wallet header for %s", url_str)
        return JSONResponse(
            status_code=402,
            content=PaymentRequiredResponse(resource_id=RESOURCE_ID).model_dump(),
        )

    try:
        paid = await mainlayer.verify_payment(x_payer_wallet)
    except MainlayerError as exc:
        logger.error("Mainlayer verification error: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"error": "payment_verification_failed", "detail": str(exc)},
        )

    if not paid:
        logger.info("Payment required for wallet %s, URL %s", x_payer_wallet, url_str)
        return JSONResponse(
            status_code=402,
            content=PaymentRequiredResponse(resource_id=RESOURCE_ID).model_dump(),
        )

    # --- 2. Rate limit check ---
    try:
        await limiter.enforce(x_payer_wallet)
    except RateLimitExceeded as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "retry_after": exc.retry_after,
                "detail": str(exc),
            },
            headers={"Retry-After": str(int(exc.retry_after))},
        )

    # --- 3. Cache lookup ---
    cached_result = await cache.get(url_str, scrape_request.options)
    if cached_result is not None:
        logger.info("Cache hit for %s (wallet %s)", url_str, x_payer_wallet)
        return cached_result

    # --- 4. Perform scrape ---
    try:
        result = await scrape(url_str, scrape_request.options)
    except ScraperError as exc:
        status = exc.status_code or 502
        logger.warning("Scrape failed for %s: %s", url_str, exc)
        return JSONResponse(
            status_code=status if status >= 500 else 502,
            content={"error": "scrape_failed", "detail": str(exc), "url": url_str},
        )

    # --- 5. Store in cache and record usage ---
    await cache.set(url_str, scrape_request.options, result)
    await mainlayer.record_usage(x_payer_wallet, url_str)

    logger.info(
        "Scraped %s in %.0fms for wallet %s",
        url_str,
        result.scrape_duration_ms or 0,
        x_payer_wallet,
    )
    return result
