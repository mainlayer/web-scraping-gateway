"""Pydantic models for the web scraping gateway."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class OutputFormat(str, Enum):
    text = "text"
    html = "html"
    markdown = "markdown"


class ScrapeOptions(BaseModel):
    format: OutputFormat = OutputFormat.text
    include_links: bool = Field(default=True, description="Include extracted hyperlinks in response")
    include_metadata: bool = Field(default=True, description="Include page metadata in response")
    timeout: int = Field(default=15, ge=1, le=60, description="Request timeout in seconds")
    follow_redirects: bool = Field(default=True, description="Follow HTTP redirects")
    user_agent: Optional[str] = Field(
        default=None,
        description="Custom User-Agent string. Defaults to a standard browser UA.",
    )


class ScrapeRequest(BaseModel):
    url: HttpUrl = Field(..., description="The URL to scrape")
    options: ScrapeOptions = Field(default_factory=ScrapeOptions)

    @field_validator("url", mode="before")
    @classmethod
    def validate_url_scheme(cls, v: Any) -> Any:
        url_str = str(v)
        if not url_str.startswith(("http://", "https://")):
            raise ValueError("URL must use http or https scheme")
        return v


class PageMetadata(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    author: Optional[str] = None
    canonical_url: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None


class ScrapeResult(BaseModel):
    url: str = Field(..., description="The final URL after any redirects")
    status_code: int = Field(..., description="HTTP status code of the scraped page")
    format: OutputFormat = Field(..., description="Output format of the content field")
    content: str = Field(..., description="Scraped page content in the requested format")
    title: Optional[str] = Field(default=None, description="Page title")
    links: List[str] = Field(default_factory=list, description="Extracted hyperlinks")
    metadata: Optional[PageMetadata] = Field(default=None, description="Page metadata")
    word_count: int = Field(default=0, description="Word count of extracted text")
    cached: bool = Field(default=False, description="Whether this result was served from cache")
    scrape_duration_ms: Optional[float] = Field(
        default=None, description="Time taken to scrape the page in milliseconds"
    )


class PaymentRequiredResponse(BaseModel):
    error: str = "payment_required"
    resource_id: str
    price_usd: float = 0.001
    pay_endpoint: str = "https://api.mainlayer.xyz/pay"
    message: str = "This endpoint costs $0.001 per page. Pay via Mainlayer to proceed."


class PricingResponse(BaseModel):
    price_per_page_usd: float = 0.001
    resource_id: str
    supported_formats: List[str] = Field(default_factory=lambda: ["text", "html", "markdown"])
    description: str = "Pay-per-page web scraping. No API key registration required."
    pay_endpoint: str = "https://api.mainlayer.xyz/pay"
    docs_url: str = "https://api.mainlayer.xyz/docs"


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    cache_entries: int = 0
    uptime_seconds: float = 0.0
