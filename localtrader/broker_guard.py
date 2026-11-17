from __future__ import annotations

from urllib.parse import urlparse


class UnsafeBrokerEndpoint(RuntimeError):
    pass


class PaperEndpointGuard:
    """Safety gate only; the MVP deliberately contains no broker transport."""

    ALLOWED_HOSTS = frozenset({"paper-api.alpaca.markets", "broker-api.sandbox.alpaca.markets"})

    @classmethod
    def verify(cls, endpoint: str, explicit_paper_confirmation: bool) -> str:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or parsed.hostname not in cls.ALLOWED_HOSTS or not explicit_paper_confirmation:
            raise UnsafeBrokerEndpoint("broker endpoint is not explicitly verified as paper/sandbox")
        return endpoint.rstrip("/")

