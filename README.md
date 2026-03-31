# Web Scraping Gateway

Pay-per-page web scraping for AI agents.

**$0.001 per page. No API key registration. Just pay and scrape.**

---

## How it works

1. Your agent checks `/pricing` to confirm the cost and resource ID.
2. Your agent pays $0.001 to Mainlayer for `resource_id: web-scraping-gateway-v1`.
3. Your agent calls `POST /scrape` with the `X-Payer-Wallet` header.
4. The gateway verifies payment, scrapes the URL, and returns clean content.

No account. No registration. No monthly subscription.

---

## Quick start

### Run locally

```bash
cp .env.example .env
# Set MAINLAYER_API_KEY in .env

pip install -r requirements.txt
uvicorn src.main:app --reload
```

### Run with Docker

```bash
cp .env.example .env
docker-compose up
```

---

## API reference

### `GET /pricing`

Returns the current price and payment instructions.

```json
{
  "price_per_page_usd": 0.001,
  "resource_id": "web-scraping-gateway-v1",
  "supported_formats": ["text", "html", "markdown"],
  "pay_endpoint": "https://api.mainlayer.xyz/pay"
}
```

### `POST /scrape`

Scrape a URL. Requires a valid payment for the payer wallet.

**Headers**

| Header | Required | Description |
|---|---|---|
| `X-Payer-Wallet` | Yes | Your Mainlayer payer wallet address |
| `Content-Type` | Yes | `application/json` |

**Request body**

```json
{
  "url": "https://example.com/article",
  "options": {
    "format": "text",
    "include_links": true,
    "include_metadata": true,
    "timeout": 15,
    "follow_redirects": true
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | URL to scrape (http/https only) |
| `options.format` | `text` \| `html` \| `markdown` | `text` | Output format |
| `options.include_links` | boolean | `true` | Extract hyperlinks |
| `options.include_metadata` | boolean | `true` | Extract page metadata |
| `options.timeout` | integer (1–60) | `15` | Request timeout in seconds |
| `options.follow_redirects` | boolean | `true` | Follow HTTP redirects |

**Response (200)**

```json
{
  "url": "https://example.com/article",
  "status_code": 200,
  "format": "text",
  "content": "Article title\n\nFull article text...",
  "title": "Article Title",
  "links": ["https://example.com/related"],
  "metadata": {
    "title": "Article Title",
    "description": "Page meta description",
    "og_image": "https://example.com/image.jpg"
  },
  "word_count": 842,
  "cached": false,
  "scrape_duration_ms": 310.4
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `402` | Payment required — pay via Mainlayer first |
| `422` | Validation error — check your request body |
| `429` | Rate limit exceeded — back off and retry |
| `502` | Upstream error — scrape failed or payment service unreachable |

### `GET /health`

Service liveness check.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "cache_entries": 12,
  "uptime_seconds": 3600.0
}
```

---

## Output formats

| Format | Description |
|---|---|
| `text` | Clean readable text, noise (scripts/styles) removed |
| `html` | Raw HTML as returned by the server |
| `markdown` | Headings, paragraphs, links, and emphasis converted to Markdown |

---

## Agent integration example

An AI agent that autonomously pays and scrapes in a single flow:

```python
import httpx

GATEWAY = "http://localhost:8000"
MAINLAYER = "https://api.mainlayer.xyz"
AGENT_WALLET = "wallet_agent_abc123"
TARGET_URL = "https://news.ycombinator.com"

# 1. Check the price
pricing = httpx.get(f"{GATEWAY}/pricing").json()
print(f"Cost: ${pricing['price_per_page_usd']} per page")

# 2. Pay via Mainlayer
payment = httpx.post(
    f"{MAINLAYER}/pay",
    json={
        "wallet": AGENT_WALLET,
        "resource_id": pricing["resource_id"],
        "amount_usd": pricing["price_per_page_usd"],
    },
    headers={"Authorization": "Bearer <mainlayer_api_key>"},
).json()

print(f"Payment status: {payment['status']}")

# 3. Scrape the URL
result = httpx.post(
    f"{GATEWAY}/scrape",
    json={
        "url": TARGET_URL,
        "options": {"format": "markdown", "include_links": True},
    },
    headers={"X-Payer-Wallet": AGENT_WALLET},
).json()

print(f"Title: {result['title']}")
print(f"Words: {result['word_count']}")
print(result["content"][:500])
```

---

## Caching

Identical requests (same URL + options) are served from an in-memory LRU cache for 5 minutes. Cached responses include `"cached": true` and are not charged again.

---

## Rate limits

- 60 requests per 60-second window per wallet
- Burst limit: 10 requests per 5 seconds
- `429` responses include a `Retry-After` header

---

## Configuration

All configuration is via environment variables. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `MAINLAYER_API_KEY` | — | Your Mainlayer API key (required) |
| `RESOURCE_ID` | `web-scraping-gateway-v1` | Mainlayer resource identifier |
| `CACHE_MAX_SIZE` | `500` | LRU cache capacity |
| `CACHE_TTL_SECONDS` | `300` | Cache entry TTL |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Requests per window per wallet |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v --cov=src

# Run with auto-reload
uvicorn src.main:app --reload
```

---

## Support

- Mainlayer: [https://mainlayer.xyz](https://mainlayer.xyz)
- API docs: [https://api.mainlayer.xyz/docs](https://api.mainlayer.xyz/docs)
