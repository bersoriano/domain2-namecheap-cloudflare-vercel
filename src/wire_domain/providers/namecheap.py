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
            raise NamecheapError(f"Failed to read nameservers for {domain}", cause=exc) from exc
        return list(result.get("Nameservers") or [])

    def set_nameservers(self, domain: str, nameservers: list[str]) -> None:
        try:
            result = self.client.domains.dns.set_custom(domain, nameservers)
        except Exception as exc:  # noqa: BLE001
            raise NamecheapError(f"Failed to set nameservers for {domain}", cause=exc) from exc
        if not result.get("IsSuccess"):
            warning = result.get("Warnings") or "unknown error"
            raise NamecheapError(f"Namecheap rejected nameserver update for {domain}: {warning}")
