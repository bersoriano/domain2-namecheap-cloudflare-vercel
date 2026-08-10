"""Cloudflare provider: get-or-create a zone and ensure Vercel DNS records."""

from __future__ import annotations

from dataclasses import dataclass

from wire_domain.config import Settings
from wire_domain.errors import CloudflareProviderError
from wire_domain.models import RecordSpec, StepStatus


@dataclass
class ZoneInfo:
    id: str
    name: str
    name_servers: list[str]
    status: str
    created: bool


def _zone_create_error(domain: str, exc: Exception) -> CloudflareProviderError:
    """Translate a raw Cloudflare zone-create failure into actionable guidance."""
    text = str(exc)
    lowered = text.lower()

    if "1061" in text or "already exists" in lowered:
        return CloudflareProviderError(
            f"A Cloudflare zone for '{domain}' already exists - most likely under a "
            "different Cloudflare account than this API token. Remove the zone from "
            "that account, or run wire-domain with that account's token and its "
            "CLOUDFLARE_ACCOUNT_ID.",
            cause=exc,
        )
    if any(code in text for code in ("9109", "1000", "10000")) or "authentication" in lowered or "permission" in lowered or "403" in text:
        return CloudflareProviderError(
            f"Cloudflare rejected the request while creating the zone for '{domain}' "
            "(authentication/permission). The API token needs Zone -> Zone -> Edit (to "
            "create zones) plus Zone -> DNS -> Edit. Check CLOUDFLARE_API_TOKEN and its "
            "scopes.",
            cause=exc,
        )
    return CloudflareProviderError(f"Failed to create zone for {domain}", cause=exc)


class CloudflareProvider:
    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self.settings = settings
        if client is None:
            from cloudflare import Cloudflare

            client = Cloudflare(api_token=settings.cloudflare_api_token)
        self.client = client

    @staticmethod
    def _fqdn(zone_name: str, name: str) -> str:
        if name in ("@", "", zone_name):
            return zone_name
        return f"{name}.{zone_name}"

    def _account_id(self) -> str:
        if self.settings.cloudflare_account_id:
            return self.settings.cloudflare_account_id
        try:
            accounts = list(self.client.accounts.list())
        except Exception as exc:  # noqa: BLE001
            raise CloudflareProviderError("Failed to list Cloudflare accounts", cause=exc) from exc
        if len(accounts) == 1:
            return accounts[0].id
        raise CloudflareProviderError(
            "Cannot determine Cloudflare account: set CLOUDFLARE_ACCOUNT_ID "
            f"(token sees {len(accounts)} accounts)."
        )

    def get_or_create_zone(self, domain: str) -> ZoneInfo:
        try:
            matches = [z for z in self.client.zones.list(name=domain) if z.name == domain]
        except Exception as exc:  # noqa: BLE001
            raise CloudflareProviderError(f"Failed to list zones for {domain}", cause=exc) from exc

        if matches:
            zone = matches[0]
            created = False
        else:
            account_id = self._account_id()
            try:
                zone = self.client.zones.create(
                    account={"id": account_id}, name=domain, type="full"
                )
            except Exception as exc:  # noqa: BLE001
                raise _zone_create_error(domain, exc) from exc
            created = True

        return ZoneInfo(
            id=zone.id,
            name=zone.name,
            name_servers=list(getattr(zone, "name_servers", None) or []),
            status=getattr(zone, "status", "unknown"),
            created=created,
        )

    def get_zone(self, domain: str) -> ZoneInfo | None:
        """Read-only: return the zone for domain if it exists, else None. Never creates."""
        try:
            matches = [z for z in self.client.zones.list(name=domain) if z.name == domain]
        except Exception as exc:  # noqa: BLE001
            raise CloudflareProviderError(f"Failed to read zone for {domain}", cause=exc) from exc
        if not matches:
            return None
        zone = matches[0]
        return ZoneInfo(
            id=zone.id,
            name=zone.name,
            name_servers=list(getattr(zone, "name_servers", None) or []),
            status=getattr(zone, "status", "unknown"),
            created=False,
        )

    def ensure_record(self, zone_id: str, zone_name: str, spec: RecordSpec) -> StepStatus:
        fqdn = self._fqdn(zone_name, spec.name)
        try:
            existing = [
                r
                for r in self.client.dns.records.list(zone_id=zone_id, type=spec.type, name=fqdn)
                if r.type == spec.type and r.name == fqdn
            ]
        except Exception as exc:  # noqa: BLE001
            raise CloudflareProviderError(f"Failed to list records for {fqdn}", cause=exc) from exc

        try:
            if not existing:
                self.client.dns.records.create(
                    zone_id=zone_id, type=spec.type, name=fqdn,
                    content=spec.content, ttl=spec.ttl, proxied=spec.proxied,
                )
                return "created"

            record = existing[0]
            if record.content == spec.content and bool(record.proxied) == spec.proxied:
                return "skipped"

            self.client.dns.records.update(
                record.id, zone_id=zone_id, type=spec.type, name=fqdn,
                content=spec.content, ttl=spec.ttl, proxied=spec.proxied,
            )
            return "updated"
        except Exception as exc:  # noqa: BLE001
            raise CloudflareProviderError(f"Failed to ensure record {spec.type} {fqdn}", cause=exc) from exc
