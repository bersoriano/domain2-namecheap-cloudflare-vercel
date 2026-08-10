"""Load and validate configuration from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import dotenv_values

from wire_domain.console import console
from wire_domain.errors import ConfigError

_REQUIRED = [
    "NAMECHEAP_API_USER",
    "NAMECHEAP_API_KEY",
    "NAMECHEAP_USERNAME",
    "NAMECHEAP_CLIENT_IP",
    "CLOUDFLARE_API_TOKEN",
    "VERCEL_TOKEN",
]
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    namecheap_api_user: str
    namecheap_api_key: str
    namecheap_username: str
    namecheap_client_ip: str
    namecheap_sandbox: bool
    cloudflare_api_token: str
    cloudflare_account_id: str | None
    vercel_token: str
    vercel_team_id: str | None
    vercel_project: str | None


def mask(value: str) -> str:
    if not value:
        return "—"
    return value[:4] + "…"


def load_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | None = None,
) -> Settings:
    """Build Settings from env. If env is None, merge .env file then os.environ
    (os.environ wins). Raises ConfigError listing every missing required var."""
    if env is None:
        merged: dict[str, str] = {}
        merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
        merged.update(os.environ)
        env = merged

    missing = [key for key in _REQUIRED if not env.get(key)]
    if missing:
        raise ConfigError(
            "Missing required configuration: " + ", ".join(missing)
        )

    def opt(key: str) -> str | None:
        value = env.get(key)
        return value or None

    return Settings(
        namecheap_api_user=env["NAMECHEAP_API_USER"],
        namecheap_api_key=env["NAMECHEAP_API_KEY"],
        namecheap_username=env["NAMECHEAP_USERNAME"],
        namecheap_client_ip=env["NAMECHEAP_CLIENT_IP"],
        namecheap_sandbox=str(env.get("NAMECHEAP_SANDBOX", "")).strip().lower() in _TRUTHY,
        cloudflare_api_token=env["CLOUDFLARE_API_TOKEN"],
        cloudflare_account_id=opt("CLOUDFLARE_ACCOUNT_ID"),
        vercel_token=env["VERCEL_TOKEN"],
        vercel_team_id=opt("VERCEL_TEAM_ID"),
        vercel_project=opt("VERCEL_PROJECT"),
    )


def render_config_table(settings: Settings) -> None:
    from rich.table import Table

    table = Table(title="wire-domain configuration", show_lines=False)
    table.add_column("Variable", style="cyan")
    table.add_column("Value (masked)", style="green")
    rows = [
        ("NAMECHEAP_API_USER", settings.namecheap_api_user),
        ("NAMECHEAP_API_KEY", mask(settings.namecheap_api_key)),
        ("NAMECHEAP_USERNAME", settings.namecheap_username),
        ("NAMECHEAP_CLIENT_IP", settings.namecheap_client_ip),
        ("NAMECHEAP_SANDBOX", str(settings.namecheap_sandbox)),
        ("CLOUDFLARE_API_TOKEN", mask(settings.cloudflare_api_token)),
        ("CLOUDFLARE_ACCOUNT_ID", settings.cloudflare_account_id or "—"),
        ("VERCEL_TOKEN", mask(settings.vercel_token)),
        ("VERCEL_TEAM_ID", settings.vercel_team_id or "—"),
        ("VERCEL_PROJECT", settings.vercel_project or "—"),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)
