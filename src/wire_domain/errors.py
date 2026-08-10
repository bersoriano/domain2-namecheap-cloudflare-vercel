"""Exception hierarchy for wire-domain. Every failure is a WireError subclass."""

from __future__ import annotations


class WireError(Exception):
    """Base error. Carries an optional underlying cause for --verbose output."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class ConfigError(WireError):
    """Missing or invalid configuration (exit code 1)."""


class NamecheapError(WireError):
    """A Namecheap operation failed."""


class CloudflareProviderError(WireError):
    """A Cloudflare operation failed."""


class VercelError(WireError):
    """A Vercel operation failed."""
