"""Mainlayer payment verification client.

Verifies that an agent's wallet has an active, paid session for this
resource before allowing access to the scraping endpoint.
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

MAINLAYER_BASE_URL = os.getenv("MAINLAYER_BASE_URL", "https://api.mainlayer.fr")
MAINLAYER_API_KEY = os.getenv("MAINLAYER_API_KEY", "")
RESOURCE_ID = os.getenv("RESOURCE_ID", "web-scraping-gateway-v1")
REQUEST_TIMEOUT = float(os.getenv("MAINLAYER_TIMEOUT", "5"))


class MainlayerError(Exception):
    """Raised when the Mainlayer API returns an unexpected error."""


class MainlayerClient:
    """Thin async HTTP client wrapping the Mainlayer payment verification API."""

    def __init__(
        self,
        base_url: str = MAINLAYER_BASE_URL,
        api_key: str = MAINLAYER_API_KEY,
        resource_id: str = RESOURCE_ID,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._resource_id = resource_id
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "web-scraping-gateway/1.0",
                },
                timeout=self._timeout,
            )
        return self._client

    async def verify_payment(self, payer_wallet: str) -> bool:
        """
        Check whether `payer_wallet` has a valid payment for RESOURCE_ID.

        Returns True if the payment is valid and the request should proceed.
        Returns False if payment is missing, expired, or insufficient.
        Raises MainlayerError on unexpected API failures.
        """
        if not payer_wallet:
            logger.debug("verify_payment called with empty wallet address")
            return False

        client = self._get_client()
        try:
            response = await client.post(
                "/verify",
                json={
                    "wallet": payer_wallet,
                    "resource_id": self._resource_id,
                },
            )
        except httpx.TimeoutException:
            logger.warning(
                "Mainlayer payment verification timed out for wallet %s", payer_wallet
            )
            # Fail open only in development — in production, fail closed.
            raise MainlayerError("Payment verification service timed out")
        except httpx.RequestError as exc:
            logger.error("Mainlayer request error: %s", exc)
            raise MainlayerError(f"Payment verification request failed: {exc}") from exc

        if response.status_code == 200:
            data: Dict[str, Any] = response.json()
            paid: bool = bool(data.get("paid", False))
            logger.debug(
                "Payment verification result for wallet %s: paid=%s", payer_wallet, paid
            )
            return paid

        if response.status_code in (401, 403):
            logger.warning(
                "Mainlayer API auth failure (status %s). Check MAINLAYER_API_KEY.",
                response.status_code,
            )
            raise MainlayerError("Invalid Mainlayer API key")

        if response.status_code == 404:
            # Wallet not found / no payment recorded
            return False

        logger.error(
            "Unexpected Mainlayer status %s: %s", response.status_code, response.text
        )
        raise MainlayerError(
            f"Unexpected Mainlayer response status {response.status_code}"
        )

    async def record_usage(self, payer_wallet: str, url: str) -> None:
        """
        Optionally notify Mainlayer that a charge has been consumed.
        This is a best-effort call — failures are logged but not raised.
        """
        client = self._get_client()
        try:
            await client.post(
                "/usage",
                json={
                    "wallet": payer_wallet,
                    "resource_id": self._resource_id,
                    "units": 1,
                    "metadata": {"url": url},
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record usage with Mainlayer: %s", exc)

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Module-level singleton
_client: Optional[MainlayerClient] = None


def get_mainlayer_client() -> MainlayerClient:
    global _client
    if _client is None:
        _client = MainlayerClient()
    return _client
