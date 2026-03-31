"""API tests for the web scraping gateway.

All HTTP calls (to scraped sites and Mainlayer) are mocked so tests run
without network access.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
  <meta name="description" content="A test page for scraping">
  <meta property="og:title" content="OG Test Page">
</head>
<body>
  <h1>Hello World</h1>
  <p>This is a test paragraph with some content.</p>
  <a href="https://example.com/page1">Link One</a>
  <a href="https://example.com/page2">Link Two</a>
</body>
</html>
"""

PAID_WALLET = "wallet_paid_abc123"
UNPAID_WALLET = "wallet_unpaid_xyz789"
RESOURCE_ID = "web-scraping-gateway-v1"


def _make_mock_mainlayer(paid: bool = True) -> MagicMock:
    client = MagicMock()
    client.verify_payment = AsyncMock(return_value=paid)
    client.record_usage = AsyncMock()
    client.aclose = AsyncMock()
    return client


def _make_mock_scrape_result():
    from src.models import OutputFormat, PageMetadata, ScrapeResult

    return ScrapeResult(
        url="https://example.com/test",
        status_code=200,
        format=OutputFormat.text,
        content="Hello World\nThis is a test paragraph with some content.",
        title="Test Page",
        links=["https://example.com/page1", "https://example.com/page2"],
        metadata=PageMetadata(title="Test Page", description="A test page for scraping"),
        word_count=11,
        cached=False,
        scrape_duration_ms=42.5,
    )


@pytest.fixture()
def app_with_mocks():
    """Return the FastAPI app with singletons replaced by mocks."""
    from src.main import app
    from src.cache import ScrapeCache
    from src.rate_limiter import SlidingWindowRateLimiter

    mock_mainlayer = _make_mock_mainlayer(paid=True)
    real_cache = ScrapeCache()
    real_limiter = SlidingWindowRateLimiter()

    app.state.mainlayer_client = mock_mainlayer
    app.state.cache = real_cache
    app.state.rate_limiter = real_limiter
    return app, mock_mainlayer, real_cache, real_limiter


@pytest.fixture()
def client(app_with_mocks):
    app, *_ = app_with_mocks
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_body(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"
        assert "cache_entries" in data
        assert "uptime_seconds" in data

    def test_health_uptime_is_float(self, client):
        resp = client.get("/health")
        assert isinstance(resp.json()["uptime_seconds"], float)


# ---------------------------------------------------------------------------
# /pricing
# ---------------------------------------------------------------------------

class TestPricing:
    def test_pricing_returns_200(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200

    def test_pricing_has_expected_fields(self, client):
        data = client.get("/pricing").json()
        assert data["price_per_page_usd"] == 0.001
        assert data["resource_id"] == RESOURCE_ID
        assert "text" in data["supported_formats"]
        assert "html" in data["supported_formats"]
        assert "markdown" in data["supported_formats"]
        assert "pay_endpoint" in data

    def test_pricing_pay_endpoint(self, client):
        data = client.get("/pricing").json()
        assert data["pay_endpoint"] == "https://api.mainlayer.fr/pay"


# ---------------------------------------------------------------------------
# /scrape — payment enforcement
# ---------------------------------------------------------------------------

class TestScrapePaymentEnforcement:
    def test_missing_wallet_header_returns_402(self, client):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 402

    def test_402_response_contains_resource_id(self, client):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        data = resp.json()
        assert data["resource_id"] == RESOURCE_ID

    def test_402_response_contains_pay_endpoint(self, client):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        data = resp.json()
        assert data["pay_endpoint"] == "https://api.mainlayer.fr/pay"

    def test_402_response_error_field(self, client):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        assert resp.json()["error"] == "payment_required"

    def test_402_price_is_correct(self, client):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        assert resp.json()["price_usd"] == 0.001

    def test_unpaid_wallet_returns_402(self, app_with_mocks):
        app, mock_mainlayer, *_ = app_with_mocks
        mock_mainlayer.verify_payment = AsyncMock(return_value=False)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/scrape",
                json={"url": "https://example.com"},
                headers={"X-Payer-Wallet": UNPAID_WALLET},
            )
        assert resp.status_code == 402


# ---------------------------------------------------------------------------
# /scrape — successful scraping
# ---------------------------------------------------------------------------

class TestScrapeSuccess:
    def test_paid_wallet_scrapes_successfully(self, app_with_mocks):
        app, mock_mainlayer, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
        assert resp.status_code == 200

    def test_scrape_returns_content(self, app_with_mocks):
        app, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                data = c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                ).json()
        assert "Hello World" in data["content"]

    def test_scrape_returns_title(self, app_with_mocks):
        app, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                data = c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                ).json()
        assert data["title"] == "Test Page"

    def test_scrape_returns_links(self, app_with_mocks):
        app, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                data = c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                ).json()
        assert len(data["links"]) == 2

    def test_scrape_records_usage(self, app_with_mocks):
        app, mock_mainlayer, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
        mock_mainlayer.record_usage.assert_called_once()

    def test_scrape_verifies_payment(self, app_with_mocks):
        app, mock_mainlayer, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
        mock_mainlayer.verify_payment.assert_called_once_with(PAID_WALLET)


# ---------------------------------------------------------------------------
# /scrape — output formats
# ---------------------------------------------------------------------------

class TestScrapeFormats:
    def _post_with_format(self, app, fmt: str):
        from src.models import OutputFormat, ScrapeResult, PageMetadata

        result = ScrapeResult(
            url="https://example.com",
            status_code=200,
            format=OutputFormat(fmt),
            content=f"<content in {fmt}>",
            title="T",
            links=[],
            word_count=3,
            cached=False,
        )
        with patch("src.main.scrape", new=AsyncMock(return_value=result)):
            with TestClient(app, raise_server_exceptions=False) as c:
                return c.post(
                    "/scrape",
                    json={"url": "https://example.com", "options": {"format": fmt}},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )

    def test_text_format(self, app_with_mocks):
        app, *_ = app_with_mocks
        resp = self._post_with_format(app, "text")
        assert resp.status_code == 200
        assert resp.json()["format"] == "text"

    def test_html_format(self, app_with_mocks):
        app, *_ = app_with_mocks
        resp = self._post_with_format(app, "html")
        assert resp.status_code == 200
        assert resp.json()["format"] == "html"

    def test_markdown_format(self, app_with_mocks):
        app, *_ = app_with_mocks
        resp = self._post_with_format(app, "markdown")
        assert resp.status_code == 200
        assert resp.json()["format"] == "markdown"


# ---------------------------------------------------------------------------
# /scrape — caching
# ---------------------------------------------------------------------------

class TestScrapeCache:
    def test_second_request_is_cached(self, app_with_mocks):
        app, mock_mainlayer, real_cache, *_ = app_with_mocks
        result = _make_mock_scrape_result()
        mock_scrape = AsyncMock(return_value=result)
        with patch("src.main.scrape", new=mock_scrape):
            with TestClient(app, raise_server_exceptions=False) as c:
                c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
                resp2 = c.post(
                    "/scrape",
                    json={"url": "https://example.com/test"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
        # scrape() should only have been called once
        assert mock_scrape.call_count == 1
        assert resp2.json()["cached"] is True


# ---------------------------------------------------------------------------
# /scrape — validation errors
# ---------------------------------------------------------------------------

class TestScrapeValidation:
    def test_invalid_url_scheme_rejected(self, client):
        resp = client.post(
            "/scrape",
            json={"url": "ftp://example.com"},
            headers={"X-Payer-Wallet": PAID_WALLET},
        )
        assert resp.status_code == 422

    def test_missing_url_field_rejected(self, client):
        resp = client.post(
            "/scrape",
            json={"options": {"format": "text"}},
            headers={"X-Payer-Wallet": PAID_WALLET},
        )
        assert resp.status_code == 422

    def test_invalid_format_rejected(self, client):
        resp = client.post(
            "/scrape",
            json={"url": "https://example.com", "options": {"format": "pdf"}},
            headers={"X-Payer-Wallet": PAID_WALLET},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /scrape — upstream scraper errors
# ---------------------------------------------------------------------------

class TestScrapeErrors:
    def test_scraper_error_returns_502(self, app_with_mocks):
        from src.scraper import ScraperError

        app, *_ = app_with_mocks
        with patch("src.main.scrape", new=AsyncMock(side_effect=ScraperError("timeout"))):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/scrape",
                    json={"url": "https://example.com"},
                    headers={"X-Payer-Wallet": PAID_WALLET},
                )
        assert resp.status_code == 502
        assert resp.json()["error"] == "scrape_failed"

    def test_mainlayer_error_returns_502(self, app_with_mocks):
        from src.mainlayer import MainlayerError

        app, mock_mainlayer, *_ = app_with_mocks
        mock_mainlayer.verify_payment = AsyncMock(side_effect=MainlayerError("timeout"))
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/scrape",
                json={"url": "https://example.com"},
                headers={"X-Payer-Wallet": PAID_WALLET},
            )
        assert resp.status_code == 502
        assert resp.json()["error"] == "payment_verification_failed"
