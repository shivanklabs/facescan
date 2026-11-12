"""Gemini query rewriter calling Google Generative Language REST API via httpx2.

Invariants:
- API key is sent only in x-goog-api-key header, never in URL or logs.
- Never sends photos, selfies, face data, user/event IDs, or personal identifiers.
- Email addresses and phone numbers are stripped via regex before sending.
- Never logs keys, URLs containing keys, or query text.
- Strict timeout; falls back to original query text on missing key, timeout, or any network error.
"""

import logging
import re
from typing import Any

import httpx2
from pydantic import SecretStr

from app.interfaces.capabilities import Capabilities
from app.interfaces.query_rewriter import QueryRewriter

logger = logging.getLogger("facescan.query_rewriter")

# Strip email addresses
_EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+", re.IGNORECASE)
# Strip phone numbers (international, Indian 10-digit, common punctuation)
_PHONE_RE = re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b[6-9]\d{9}\b")


def sanitize_query_text(text: str) -> str:
    """
    Purpose: strip email addresses, phone numbers, and excess whitespace.
    Inputs: raw text. Outputs: sanitized text.
    Side effects: none.
    """
    cleaned = _EMAIL_RE.sub("", text)
    cleaned = _PHONE_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip()


class GeminiQueryRewriter(QueryRewriter):
    """Rewrites search descriptions using Gemini REST API via httpx2."""

    def __init__(
        self,
        api_key: SecretStr | None,
        model: str = "gemini-3.5-flash-lite",
        timeout_seconds: float = 2.5,
    ) -> None:
        """
        Purpose: initialize Gemini query rewriter with configuration.
        Inputs: optional api_key, model name, timeout in seconds.
        Outputs: none. Side effects: none.
        """
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def capabilities(self) -> Capabilities:
        """Static description: external REST call via httpx2."""
        return Capabilities(adapter_name="gemini")

    async def rewrite_query(self, query: str) -> list[str]:
        """
        Purpose: rewrite query to short visual search phrases via Gemini REST API.
        Inputs: query string.
        Outputs: list of 1-3 short English search phrases (falls back to [query]).
        Side effects: external HTTP request; no sensitive data logged.
        """
        sanitized = sanitize_query_text(query)
        if not sanitized:
            return []

        if self._api_key is None or not self._api_key.get_secret_value().strip():
            return [sanitized]

        prompt = (
            "Rewrite this scene description into 1 to 3 short English search phrases "
            "(separated by commas) describing visual scenes, clothing, colors, or surroundings. "
            "Output only the comma-separated English search phrases:\n\n"
            f"{sanitized}"
        )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        )
        headers = {
            "x-goog-api-key": self._api_key.get_secret_value(),
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ]
        }

        try:
            async with httpx2.AsyncClient(timeout=self._timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code != 200:
                    logger.warning(
                        "gemini rewrite returned HTTP %d, falling back", resp.status_code
                    )
                    return [sanitized]

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return [sanitized]

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts:
                    return [sanitized]

                raw_text = str(parts[0].get("text", "")).strip()
                if not raw_text:
                    return [sanitized]

                # Parse comma-separated phrases
                phrases = [
                    p.strip().lower() for p in raw_text.replace("\n", ",").split(",") if p.strip()
                ]
                return phrases[:3] if phrases else [sanitized]

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gemini rewrite failed (%s), falling back to local query", type(exc).__name__
            )
            return [sanitized]
