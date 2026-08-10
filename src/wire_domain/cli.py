"""Typer CLI for wire-domain."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from wire_domain.config import load_settings, render_config_table, Settings
from wire_domain.console import console, err_console
from wire_domain.errors import ConfigError, WireError
from wire_domain.flow import (
    Orchestrator,
    WirePlan,
    render_next_steps,
    render_report,
    vercel_record_specs,
)
from wire_domain.providers.cloudflare import CloudflareProvider
from wire_domain.providers.namecheap import NamecheapProvider
from wire_domain.providers.vercel import VercelProvider
from wire_domain.state import StateStore, default_state_dir

app = typer.Typer(help="Wire an already-registered domain: Namecheap -> Cloudflare -> Vercel.")

_PROPAGATION_NOTE = (
    "Nameserver changes can take minutes to 48 hours to propagate. "
    "The Cloudflare zone stays 'pending' until propagation completes."
)


def build_orchestrator(settings: Settings, state_dir: Path) -> Orchestrator:
    return Orchestrator(
        settings=settings,
        state_store=StateStore(state_dir),
        namecheap=NamecheapProvider(settings),
        cloudflare=CloudflareProvider(settings),
        vercel_factory=lambda project: VercelProvider(settings, project=project),
    )


def _exit_code_for(exc: WireError) -> int:
    if isinstance(exc, ConfigError):
        return 1
    return 2


def _render_error(exc: WireError, verbose: bool) -> None:
    if verbose:
        err_console.print_exception()
    err_console.print(f"[red]error:[/red] {exc}")


def _confirm_nameservers(domain: str, current: list[str], target: list[str]) -> bool:
    """Interactive gate shown right before the nameserver change."""
    console.print(f"\n[bold yellow]About to change nameservers[/bold yellow] for [bold]{domain}[/bold]:")
    console.print(f"  current: {current or '(none / registrar default)'}")
    console.print(f"  new:     {target}")
    console.print("[dim]This redirects the domain's DNS authority to Cloudflare and can take up to 48h to propagate.[/dim]")
    return typer.confirm("Change nameservers now?")


@app.command()
def wire(
    domain: str = typer.Argument(..., help="The already-registered domain to wire."),
    project: Optional[str] = typer.Option(None, "--project", help="Vercel project (or VERCEL_PROJECT)."),
    www: bool = typer.Option(True, "--www/--no-www", help="Also create www CNAME and add www to Vercel."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print intended changes, make none."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
    state_dir: Path = typer.Option(default_state_dir(), "--state-dir", help="State file directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Full tracebacks."),
) -> None:
    try:
        settings = load_settings()
        resolved_project = project or settings.vercel_project
        if not resolved_project:
            raise ConfigError("No Vercel project given. Pass --project or set VERCEL_PROJECT.")

        render_config_table(settings)
        console.print(f"Wiring [bold]{domain}[/bold] -> Vercel project [bold]{resolved_project}[/bold] "
                      f"({'with' if www else 'without'} www){' [dim](dry-run)[/dim]' if dry_run else ''}")
        for spec in vercel_record_specs(www):
            console.print(f"  DNS: {spec.type} {spec.name} -> {spec.content} (proxied={spec.proxied})")
        console.print("  Nameservers: will be set to the zone's Cloudflare nameservers (resolved during the run)")

        orch = build_orchestrator(settings, state_dir)
        # Gate the one destructive, hard-to-reverse step (the nameserver change)
        # behind an explicit confirmation that shows the exact old -> new values.
        # --yes and --dry-run skip it. Zone creation and DNS records are benign,
        # idempotent, and have no effect until nameservers point at Cloudflare.
        if not yes and not dry_run:
            orch.confirm_nameservers = _confirm_nameservers

        report = orch.wire(WirePlan(domain=domain, project=resolved_project, include_www=www, dry_run=dry_run))
        render_report(report, note=_PROPAGATION_NOTE)
        if not dry_run:
            render_next_steps(report, resolved_project)
        raise typer.Exit(code=0 if report.ok else 2)
    except ConfigError as exc:
        _render_error(exc, verbose)
        raise typer.Exit(code=1)
    except WireError as exc:
        _render_error(exc, verbose)
        raise typer.Exit(code=_exit_code_for(exc))


@app.command()
def status(
    domain: str = typer.Argument(..., help="Domain to inspect."),
    project: Optional[str] = typer.Option(None, "--project", help="Vercel project (or VERCEL_PROJECT)."),
    www: bool = typer.Option(True, "--www/--no-www", help="Also check www."),
    state_dir: Path = typer.Option(default_state_dir(), "--state-dir", help="State file directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Full tracebacks."),
) -> None:
    try:
        settings = load_settings()
        resolved_project = project or settings.vercel_project
        if not resolved_project:
            raise ConfigError("No Vercel project given. Pass --project or set VERCEL_PROJECT.")
        orch = build_orchestrator(settings, state_dir)
        report = orch.status(domain, resolved_project, include_www=www)
        render_report(report)
        raise typer.Exit(code=0)
    except ConfigError as exc:
        _render_error(exc, verbose)
        raise typer.Exit(code=1)
    except WireError as exc:
        _render_error(exc, verbose)
        raise typer.Exit(code=_exit_code_for(exc))
