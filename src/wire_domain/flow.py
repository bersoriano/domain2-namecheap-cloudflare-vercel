"""Orchestrate the wire flow across the three providers, idempotently."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from wire_domain.config import Settings
from wire_domain.console import console
from wire_domain.errors import (
    CloudflareProviderError,
    NamecheapError,
    VercelError,
    WireError,
)
from wire_domain.models import RecordSpec, StepResult, WireReport
from wire_domain.providers.cloudflare import CloudflareProvider
from wire_domain.providers.namecheap import NamecheapProvider, nameservers_equal
from wire_domain.providers.vercel import VercelProvider
from wire_domain.state import StateStore

_APEX = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False, ttl=1)
_WWW = RecordSpec(type="CNAME", name="www", content="cname.vercel-dns.com", proxied=False, ttl=1)

_STATUS_STYLE = {
    "created": "green",
    "updated": "yellow",
    "skipped": "dim",
    "pending": "cyan",
    "failed": "red",
}


def vercel_record_specs(include_www: bool) -> list[RecordSpec]:
    return [_APEX, _WWW] if include_www else [_APEX]


@dataclass
class WirePlan:
    domain: str
    project: str
    include_www: bool
    dry_run: bool


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        state_store: StateStore,
        namecheap: NamecheapProvider,
        cloudflare: CloudflareProvider,
        vercel_factory: Callable[[str], VercelProvider],
    ) -> None:
        self.settings = settings
        self.state_store = state_store
        self.namecheap = namecheap
        self.cloudflare = cloudflare
        self.vercel_factory = vercel_factory

    def wire(self, plan: WirePlan) -> WireReport:
        report = WireReport(domain=plan.domain)
        state = self.state_store.load(plan.domain)
        try:
            zone = self.cloudflare.get_or_create_zone(plan.domain)
            report.add(StepResult(
                name="cloudflare-zone",
                status="skipped" if not zone.created else "created",
                detail=f"zone {zone.id} ({zone.status})",
            ))
            state.zone_id = zone.id
            state.cloudflare_nameservers = zone.name_servers
            if not plan.dry_run:
                state.last_completed_step = "cloudflare-zone"
                self.state_store.save(state)

            current_ns = self.namecheap.get_nameservers(plan.domain)
            if nameservers_equal(current_ns, zone.name_servers):
                report.add(StepResult("namecheap-nameservers", "skipped", "already pointing to Cloudflare"))
            elif plan.dry_run:
                report.add(StepResult("namecheap-nameservers", "pending", f"would set -> {zone.name_servers}"))
            else:
                self.namecheap.set_nameservers(plan.domain, zone.name_servers)
                state.nameservers_changed_at = datetime.now(timezone.utc).isoformat()
                state.last_completed_step = "namecheap-nameservers"
                self.state_store.save(state)
                report.add(StepResult("namecheap-nameservers", "updated", f"set -> {zone.name_servers}"))

            for spec in vercel_record_specs(plan.include_www):
                label = f"cloudflare-record:{spec.type}:{spec.name}"
                if plan.dry_run:
                    report.add(StepResult(label, "pending", f"would ensure {spec.content}"))
                else:
                    status = self.cloudflare.ensure_record(zone.id, zone.name, spec)
                    report.add(StepResult(label, status, spec.content))

            vercel = self.vercel_factory(plan.project)
            try:
                names = [plan.domain] + ([f"www.{plan.domain}"] if plan.include_www else [])
                for name in names:
                    if plan.dry_run:
                        report.add(StepResult(f"vercel-domain:{name}", "pending", f"would add to {plan.project}"))
                    else:
                        status = vercel.add_domain(name)
                        report.add(StepResult(f"vercel-domain:{name}", status, f"project {plan.project}"))
            finally:
                vercel.close()

            if not plan.dry_run:
                state.last_completed_step = "vercel"
                self.state_store.save(state)
        except WireError as exc:
            report.add(StepResult(_failed_step_name(exc), "failed", str(exc)))
        return report

    def status(self, domain: str, project: str, include_www: bool) -> WireReport:
        report = WireReport(domain=domain)
        # Read-only zone lookup (status must never create a zone).
        zone = self._read_zone(domain)
        if zone is None:
            report.add(StepResult("cloudflare-zone", "pending", "no zone found"))
            return report
        report.add(StepResult("cloudflare-zone", "skipped", f"zone {zone.id} ({zone.status})"))

        current_ns = self.namecheap.get_nameservers(domain)
        if nameservers_equal(current_ns, zone.name_servers):
            report.add(StepResult("namecheap-nameservers", "skipped", "pointing to Cloudflare"))
        else:
            report.add(StepResult("namecheap-nameservers", "pending", f"currently {current_ns}"))

        vercel = self.vercel_factory(project)
        try:
            attached = set(vercel.list_domains())
        finally:
            vercel.close()
        for name in [domain] + ([f"www.{domain}"] if include_www else []):
            status = "skipped" if name in attached else "pending"
            report.add(StepResult(f"vercel-domain:{name}", status, "attached" if name in attached else "not attached"))
        return report

    def _read_zone(self, domain: str):
        # Read-only: never creates a zone.
        return self.cloudflare.get_zone(domain)


def _failed_step_name(exc: WireError) -> str:
    if isinstance(exc, NamecheapError):
        return "namecheap-nameservers"
    if isinstance(exc, CloudflareProviderError):
        return "cloudflare"
    if isinstance(exc, VercelError):
        return "vercel"
    return "wire"


def render_report(report: WireReport, note: str | None = None) -> None:
    from rich.table import Table

    table = Table(title=f"wire-domain — {report.domain}")
    table.add_column("Step", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Detail", style="white")
    for step in report.steps:
        style = _STATUS_STYLE.get(step.status, "white")
        table.add_row(step.name, f"[{style}]{step.status}[/{style}]", step.detail)
    console.print(table)
    if note:
        console.print(f"[dim]{note}[/dim]")
