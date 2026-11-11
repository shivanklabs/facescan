"""Port: query rewriter for scene search descriptions."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class QueryRewriter(Protocol):
    """Rewrites guest typed search description into short visual search phrases."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the rewriter adapter."""
        ...

    async def rewrite_query(self, query: str) -> list[str]:
        """
        Purpose: rewrite English, Hindi, or Hinglish query into 1-3 short English visual phrases.
        Falls back to returning [query] if disabled, missing key, or on any error.
        Side effects: external network call (if active); never logs key, URL with key,
            or query text.
        """
        ...
