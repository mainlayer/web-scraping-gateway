"""Core scraping logic using httpx + BeautifulSoup4.

Fetches a URL and returns structured content in text, HTML, or Markdown format.
"""

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .models import OutputFormat, PageMetadata, ScrapeOptions, ScrapeResult

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; Mainlayer-Scraper/1.0; +https://mainlayer.xyz)"
)

# Tags whose text content we discard when extracting clean text
_NOISE_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "img",
    "video", "audio", "canvas", "figure",
}


class ScraperError(Exception):
    """Raised when the scraper cannot retrieve or parse a URL."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# HTML → text / markdown helpers
# ---------------------------------------------------------------------------

def _extract_text(soup: BeautifulSoup) -> str:
    """Extract clean readable text from a parsed document."""
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    body = soup.find("body") or soup
    lines: List[str] = []
    for element in body.descendants:
        if not isinstance(element, str):
            continue
        stripped = element.strip()
        if stripped:
            lines.append(stripped)

    return "\n".join(lines)


def _to_markdown(soup: BeautifulSoup, base_url: str) -> str:
    """Convert the document's main content to approximate Markdown."""
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    body = soup.find("body") or soup
    parts: List[str] = []

    for element in body.find_all(True):
        name = element.name
        text = element.get_text(" ", strip=True)

        if not text:
            continue

        if name in ("h1",):
            parts.append(f"# {text}\n")
        elif name in ("h2",):
            parts.append(f"## {text}\n")
        elif name in ("h3",):
            parts.append(f"### {text}\n")
        elif name in ("h4", "h5", "h6"):
            parts.append(f"#### {text}\n")
        elif name == "p":
            parts.append(f"{text}\n")
        elif name in ("li",):
            parts.append(f"- {text}")
        elif name == "a":
            href = element.get("href", "")
            if href and not href.startswith("#"):
                abs_href = urljoin(base_url, href)
                parts.append(f"[{text}]({abs_href})")
        elif name in ("code", "pre"):
            parts.append(f"`{text}`")
        elif name in ("strong", "b"):
            parts.append(f"**{text}**")
        elif name in ("em", "i"):
            parts.append(f"*{text}*")
        elif name in ("blockquote",):
            parts.append(f"> {text}\n")
        elif name in ("hr",):
            parts.append("---\n")

    # Deduplicate consecutive identical lines
    seen: List[str] = []
    for part in parts:
        if not seen or part != seen[-1]:
            seen.append(part)

    return "\n".join(seen)


def _extract_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Extract all unique absolute hyperlinks from the document."""
    links: List[str] = []
    seen: set = set()
    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def _extract_metadata(soup: BeautifulSoup) -> PageMetadata:
    """Pull common <meta> tags and Open Graph properties."""

    def _meta(name: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag):
            return tag.get("content")  # type: ignore[return-value]
        return None

    def _og(prop: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"property": f"og:{prop}"})
        if isinstance(tag, Tag):
            return tag.get("content")  # type: ignore[return-value]
        return None

    def _link_canonical() -> Optional[str]:
        tag = soup.find("link", attrs={"rel": "canonical"})
        if isinstance(tag, Tag):
            return tag.get("href")  # type: ignore[return-value]
        return None

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    return PageMetadata(
        title=title,
        description=_meta("description"),
        keywords=_meta("keywords"),
        author=_meta("author"),
        canonical_url=_link_canonical(),
        og_title=_og("title"),
        og_description=_og("description"),
        og_image=_og("image"),
    )


# ---------------------------------------------------------------------------
# Public scrape function
# ---------------------------------------------------------------------------

async def scrape(url: str, options: ScrapeOptions) -> ScrapeResult:
    """
    Fetch `url` and return a structured ScrapeResult.

    Raises ScraperError on network or HTTP errors.
    """
    headers = {
        "User-Agent": options.user_agent or DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            follow_redirects=options.follow_redirects,
            timeout=options.timeout,
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise ScraperError(f"Request timed out after {options.timeout}s: {url}") from exc
    except httpx.TooManyRedirects as exc:
        raise ScraperError(f"Too many redirects for URL: {url}") from exc
    except httpx.RequestError as exc:
        raise ScraperError(f"Network error fetching {url}: {exc}") from exc

    elapsed_ms = (time.monotonic() - start) * 1000
    final_url = str(response.url)

    if response.status_code >= 400:
        raise ScraperError(
            f"HTTP {response.status_code} for URL: {url}",
            status_code=response.status_code,
        )

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
        logger.warning("Non-HTML content type '%s' for %s", content_type, url)

    soup = BeautifulSoup(response.text, "html.parser")

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    # Content in requested format
    if options.format == OutputFormat.html:
        content = response.text
    elif options.format == OutputFormat.markdown:
        content = _to_markdown(soup, final_url)
    else:
        content = _extract_text(soup)

    word_count = len(re.findall(r"\w+", content))

    links: List[str] = []
    if options.include_links:
        links = _extract_links(soup, final_url)

    metadata: Optional[PageMetadata] = None
    if options.include_metadata:
        metadata = _extract_metadata(soup)

    return ScrapeResult(
        url=final_url,
        status_code=response.status_code,
        format=options.format,
        content=content,
        title=title,
        links=links,
        metadata=metadata,
        word_count=word_count,
        cached=False,
        scrape_duration_ms=round(elapsed_ms, 2),
    )
