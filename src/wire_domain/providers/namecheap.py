"""Namecheap provider: read and set a domain's nameservers.

Wraps adriangalilea/namecheap-python (namecheap.NamecheapClient). The library
takes the full domain name and handles SLD/TLD splitting internally.
"""

from __future__ import annotations

from typing import Iterable

from wire_domain.config import Settings
from wire_domain.errors import NamecheapError


def _norm(ns: str) -> str:
    return ns.strip().rstrip(".").lower()


def nameservers_equal(a: Iterable[str], b: Iterable[str]) -> bool:
    return {_norm(x) for x in a} == {_norm(x) for x in b}


def _namecheap_error(domain: str, action: str, exc: Exception) -> NamecheapError:
    """Translate a raw Namecheap failure into an actionable NamecheapError.

    Namecheap surfaces failures as text/error-numbers; map the common ones to
    guidance the user can act on, and fall back to a generic message.
    """
    text = str(exc)
    lowered = text.lower()

    if "1011150" in text or "invalid request ip" in lowered:
        return NamecheapError(
            "Namecheap rejected the request because your API client IP is not "
            "whitelisted. Add your current public IP under Namecheap -> Profile -> "
            "Tools -> Business & Dev Tools -> API Access -> Whitelisted IPs, and make "
            "sure NAMECHEAP_CLIENT_IP matches it (whitelist changes take a few "
            "minutes to apply).",
            cause=exc,
        )
    if "1010900" in text or "api key is invalid" in lowered:
        return NamecheapError(
            "Namecheap rejected the API credentials. Confirm API access is enabled "
            "for the account and that NAMECHEAP_API_KEY / NAMECHEAP_API_USER are correct.",
            cause=exc,
        )
    if any(code in text for code in ("2019166", "2016166", "5019169")) or "not found" in lowered:
        return NamecheapError(
            f"Namecheap could not act on '{domain}' - it does not appear to be in "
            "this account (it may be registered by someone else, or under a different "
            "Namecheap login). wire-domain only manages domains you already own.",
            cause=exc,
        )
    return NamecheapError(f"Failed to {action} for {domain}", cause=exc)


class NamecheapProvider:
    def __init__(self, settings: Settings, client: object | None = None) -> None:
        if client is None:
            from namecheap import NamecheapClient

            client = NamecheapClient(
                api_user=settings.namecheap_api_user,
                api_key=settings.namecheap_api_key,
                username=settings.namecheap_username,
                client_ip=settings.namecheap_client_ip,
                sandbox=settings.namecheap_sandbox,
                load_env=False,
            )
        self.client = client

    def get_nameservers(self, domain: str) -> list[str]:
        try:
            result = self.client.domains.dns.get_list(domain)
        except Exception as exc:  # noqa: BLE001 - normalize all SDK failures
            raise _namecheap_error(domain, "read nameservers", exc) from exc
        return list(result.get("Nameservers") or [])

    def set_nameservers(self, domain: str, nameservers: list[str]) -> None:
        try:
            result = self.client.domains.dns.set_custom(domain, nameservers)
        except Exception as exc:  # noqa: BLE001
            raise _namecheap_error(domain, "set nameservers", exc) from exc
        if not result.get("IsSuccess"):
            warning = result.get("Warnings") or "unknown error"
            raise NamecheapError(f"Namecheap rejected nameserver update for {domain}: {warning}")
