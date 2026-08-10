# wire-domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready `wire-domain` CLI that wires an already-registered domain through Namecheap (nameservers) → Cloudflare (authoritative DNS + Vercel records) → Vercel (project domain), idempotently.

**Architecture:** A Typer CLI (`cli.py`) validates `.env` config (`config.py`), then an orchestrator (`flow.py`) runs four idempotent steps by delegating to three thin provider wrappers (`providers/`). Each wrapper isolates one SDK/API and returns plain dataclasses so the orchestrator and tests never touch SDK internals. A per-domain JSON state file (`state.py`) is a cache/audit trail; live provider detection is authoritative.

**Tech Stack:** Python 3.11+, Typer, rich, python-dotenv, namecheap-python (Adrian Galilea, v0.2.x), official `cloudflare` SDK, httpx (Vercel REST). Packaged with uv + `pyproject.toml`, `src/` layout. pytest for tests.

## Global Constraints

- Python **3.11+** required (`requires-python = ">=3.11"`).
- Dependencies are fixed: `typer`, `rich`, `python-dotenv`, `namecheap-python`, `cloudflare`, `httpx`. Do not add other runtime deps. Dev deps: `pytest`.
- Console script entry point: `wire-domain = "wire_domain.cli:app"`.
- Domain **registration is out of scope** — domains are assumed already in the Namecheap account.
- DNS records to create: `A @ → 76.76.21.21` (proxied=false), `CNAME www → cname.vercel-dns.com` (proxied=false).
- Cloudflare records use `ttl=1` (means "automatic").
- No real network calls in tests: httpx `MockTransport` for Vercel; monkeypatched fake objects for Cloudflare & Namecheap SDKs.
- Every command must fail with exit code `1` on config error, `2` on step failure, `0` on success.
- Secrets are always masked when displayed (show first 4 chars + `…`).
- Commit after every task with the message shown in that task's final step.

---

### Task 1: Project scaffold (uv package, buildable, importable)

**Files:**
- Create: `pyproject.toml`
- Create: `src/wire_domain/__init__.py`
- Create: `src/wire_domain/__main__.py`
- Create: `.env.example`
- Create: `README.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: `wire_domain.__version__: str`; module runnable via `python -m wire_domain` (delegates to `wire_domain.cli:app`, added in Task 6 — until then `__main__.py` imports lazily).

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:
```python
import wire_domain


def test_has_version():
    assert isinstance(wire_domain.__version__, str)
    assert wire_domain.__version__.count(".") >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: FAIL (module `wire_domain` not importable / no `__version__`).

- [ ] **Step 3: Write the scaffold**

`pyproject.toml`:
```toml
[project]
name = "wire-domain"
version = "0.1.0"
description = "Wire an already-registered domain: Namecheap -> Cloudflare -> Vercel."
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "python-dotenv>=1.0",
    "namecheap-python>=0.2,<0.3",
    "cloudflare>=4.0",
    "httpx>=0.27",
]

[project.scripts]
wire-domain = "wire_domain.cli:app"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wire_domain"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]
```

`src/wire_domain/__init__.py`:
```python
"""wire-domain: wire an already-registered domain across Namecheap, Cloudflare, and Vercel."""

__version__ = "0.1.0"
```

`src/wire_domain/__main__.py`:
```python
from wire_domain.cli import app

if __name__ == "__main__":
    app()
```

`.env.example`:
```dotenv
# Namecheap (registrar) — domain must already exist in this account
NAMECHEAP_API_USER=your_api_user
NAMECHEAP_API_KEY=your_api_key
NAMECHEAP_USERNAME=your_username
NAMECHEAP_CLIENT_IP=your_whitelisted_public_ip
NAMECHEAP_SANDBOX=false

# Cloudflare (authoritative DNS)
CLOUDFLARE_API_TOKEN=your_token_with_zone_edit_and_dns_edit
# Optional: only needed if the token can see multiple accounts
CLOUDFLARE_ACCOUNT_ID=

# Vercel (hosting)
VERCEL_TOKEN=your_vercel_token
# Optional
VERCEL_TEAM_ID=
VERCEL_PROJECT=
```

`README.md` (minimal, expanded in Task 9):
```markdown
# wire-domain

Wire an already-registered domain: **Namecheap → Cloudflare → Vercel**.

## Install
```bash
uv sync
```

## Usage
```bash
uv run wire-domain wire example.com --project my-vercel-project
uv run wire-domain status example.com
```

See `.env.example` for required configuration.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS. (`uv run` auto-creates the venv and installs deps.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/wire_domain/__init__.py src/wire_domain/__main__.py .env.example README.md tests/test_smoke.py
git commit -m "feat: scaffold wire-domain uv package"
```

---

### Task 2: Errors + models (shared dataclasses and exception hierarchy)

**Files:**
- Create: `src/wire_domain/errors.py`
- Create: `src/wire_domain/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `WireError(Exception)` base; subclasses `ConfigError`, `NamecheapError`, `CloudflareProviderError`, `VercelError`. Each takes `(message: str, cause: Exception | None = None)`.
  - `StepStatus` = `Literal["created", "updated", "skipped", "failed", "pending"]`.
  - `@dataclass StepResult(name: str, status: StepStatus, detail: str = "")`.
  - `@dataclass RecordSpec(type: str, name: str, content: str, proxied: bool, ttl: int = 1)`.
  - `@dataclass WireReport(domain: str, steps: list[StepResult] = field(default_factory=list))` with method `add(self, result: StepResult) -> None` and property `ok: bool` (True iff no step has status `"failed"`).

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
import pytest

from wire_domain.errors import (
    CloudflareProviderError,
    ConfigError,
    NamecheapError,
    VercelError,
    WireError,
)
from wire_domain.models import RecordSpec, StepResult, WireReport


def test_error_hierarchy_and_cause():
    cause = ValueError("boom")
    err = NamecheapError("failed to set ns", cause=cause)
    assert isinstance(err, WireError)
    assert err.cause is cause
    assert "failed to set ns" in str(err)
    for cls in (ConfigError, CloudflareProviderError, VercelError):
        assert issubclass(cls, WireError)


def test_report_ok_and_add():
    report = WireReport(domain="example.com")
    assert report.ok is True
    report.add(StepResult(name="cloudflare-zone", status="created"))
    report.add(StepResult(name="namecheap-ns", status="skipped", detail="already set"))
    assert report.ok is True
    report.add(StepResult(name="vercel", status="failed", detail="401"))
    assert report.ok is False
    assert len(report.steps) == 3


def test_record_spec_defaults():
    spec = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
    assert spec.ttl == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL (modules not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/errors.py`:
```python
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
```

`src/wire_domain/models.py`:
```python
"""Plain dataclasses passed between providers, the orchestrator, and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["created", "updated", "skipped", "failed", "pending"]


@dataclass
class StepResult:
    name: str
    status: StepStatus
    detail: str = ""


@dataclass
class RecordSpec:
    type: str
    name: str
    content: str
    proxied: bool
    ttl: int = 1


@dataclass
class WireReport:
    domain: str
    steps: list[StepResult] = field(default_factory=list)

    def add(self, result: StepResult) -> None:
        self.steps.append(result)

    @property
    def ok(self) -> bool:
        return all(step.status != "failed" for step in self.steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/errors.py src/wire_domain/models.py tests/test_models.py
git commit -m "feat: add error hierarchy and shared models"
```

---

### Task 3: Config loading + validation with masked rich table

**Files:**
- Create: `src/wire_domain/console.py`
- Create: `src/wire_domain/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `ConfigError` from `wire_domain.errors`.
- Produces:
  - `console.py`: module-level `console: rich.console.Console` and `err_console: rich.console.Console` (stderr).
  - `config.py`:
    - `@dataclass(frozen=True) Settings` with fields: `namecheap_api_user: str`, `namecheap_api_key: str`, `namecheap_username: str`, `namecheap_client_ip: str`, `namecheap_sandbox: bool`, `cloudflare_api_token: str`, `cloudflare_account_id: str | None`, `vercel_token: str`, `vercel_team_id: str | None`, `vercel_project: str | None`.
    - `mask(value: str) -> str` — returns first 4 chars + `"…"`, or `"—"` if empty.
    - `load_settings(env: Mapping[str, str] | None = None, dotenv_path: str | None = None) -> Settings` — loads `.env` (via `python-dotenv`) unless `env` is provided (tests pass a dict to avoid file/OS env). Raises `ConfigError` listing all missing required vars.
    - `render_config_table(settings: Settings) -> None` — prints a masked rich table of every variable.

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import pytest

from wire_domain.config import Settings, load_settings, mask
from wire_domain.errors import ConfigError

REQUIRED = {
    "NAMECHEAP_API_USER": "user1",
    "NAMECHEAP_API_KEY": "key123456",
    "NAMECHEAP_USERNAME": "user1",
    "NAMECHEAP_CLIENT_IP": "1.2.3.4",
    "CLOUDFLARE_API_TOKEN": "cftoken123",
    "VERCEL_TOKEN": "vtoken123",
}


def test_mask_short_and_empty():
    assert mask("") == "—"
    assert mask("abcdef") == "abcd…"


def test_load_settings_happy_path():
    s = load_settings(env=REQUIRED)
    assert isinstance(s, Settings)
    assert s.namecheap_api_user == "user1"
    assert s.namecheap_sandbox is False  # default
    assert s.cloudflare_account_id is None
    assert s.vercel_project is None


def test_sandbox_parsed_truthy():
    s = load_settings(env={**REQUIRED, "NAMECHEAP_SANDBOX": "true"})
    assert s.namecheap_sandbox is True


def test_optional_fields_passthrough():
    s = load_settings(
        env={**REQUIRED, "VERCEL_TEAM_ID": "team_x", "VERCEL_PROJECT": "proj_y", "CLOUDFLARE_ACCOUNT_ID": "acct_z"}
    )
    assert s.vercel_team_id == "team_x"
    assert s.vercel_project == "proj_y"
    assert s.cloudflare_account_id == "acct_z"


def test_missing_required_lists_all():
    with pytest.raises(ConfigError) as exc:
        load_settings(env={"NAMECHEAP_API_USER": "u"})
    msg = str(exc.value)
    assert "NAMECHEAP_API_KEY" in msg
    assert "CLOUDFLARE_API_TOKEN" in msg
    assert "VERCEL_TOKEN" in msg
    assert "NAMECHEAP_API_USER" not in msg  # provided, not missing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/console.py`:
```python
"""Shared rich consoles. stdout for normal output, stderr for errors."""

from rich.console import Console

console = Console()
err_console = Console(stderr=True)
```

`src/wire_domain/config.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/console.py src/wire_domain/config.py tests/test_config.py
git commit -m "feat: add config loading with masked validation table"
```

---

### Task 4: State store (per-domain resumable JSON)

**Files:**
- Create: `src/wire_domain/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass DomainState` with fields: `domain: str`, `zone_id: str | None = None`, `cloudflare_nameservers: list[str] = field(default_factory=list)`, `nameservers_changed_at: str | None = None`, `last_completed_step: str | None = None`, `updated_at: str | None = None`.
  - `class StateStore` constructed as `StateStore(state_dir: Path)`.
    - `path_for(domain: str) -> Path` → `state_dir / f"{domain}.json"`.
    - `load(domain: str) -> DomainState` → returns default `DomainState(domain)` if the file is missing or unreadable (tolerant).
    - `save(state: DomainState) -> None` → creates `state_dir` if needed, sets `updated_at` to current UTC ISO-8601, writes pretty JSON.
  - `default_state_dir() -> Path` → `Path.home() / ".wire-domain" / "state"`.

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
from pathlib import Path

from wire_domain.state import DomainState, StateStore, default_state_dir


def test_load_missing_returns_default(tmp_path: Path):
    store = StateStore(tmp_path)
    state = store.load("example.com")
    assert isinstance(state, DomainState)
    assert state.domain == "example.com"
    assert state.zone_id is None
    assert state.cloudflare_nameservers == []


def test_save_then_load_roundtrip(tmp_path: Path):
    store = StateStore(tmp_path)
    state = DomainState(domain="example.com", zone_id="zone123")
    state.cloudflare_nameservers = ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    state.last_completed_step = "cloudflare-zone"
    store.save(state)

    assert store.path_for("example.com") == tmp_path / "example.com.json"
    loaded = store.load("example.com")
    assert loaded.zone_id == "zone123"
    assert loaded.cloudflare_nameservers == ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    assert loaded.last_completed_step == "cloudflare-zone"
    assert loaded.updated_at is not None  # stamped on save


def test_save_creates_dir(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    store = StateStore(nested)
    store.save(DomainState(domain="x.com"))
    assert (nested / "x.com.json").exists()


def test_default_state_dir_shape():
    d = default_state_dir()
    assert d.name == "state"
    assert d.parent.name == ".wire-domain"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/state.py`:
```python
"""Per-domain resumable state. A cache/audit trail; live provider state wins."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class DomainState:
    domain: str
    zone_id: str | None = None
    cloudflare_nameservers: list[str] = field(default_factory=list)
    nameservers_changed_at: str | None = None
    last_completed_step: str | None = None
    updated_at: str | None = None


def default_state_dir() -> Path:
    return Path.home() / ".wire-domain" / "state"


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)

    def path_for(self, domain: str) -> Path:
        return self.state_dir / f"{domain}.json"

    def load(self, domain: str) -> DomainState:
        path = self.path_for(domain)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return DomainState(domain=domain)
        data.setdefault("domain", domain)
        known = DomainState.__dataclass_fields__.keys()
        return DomainState(**{k: v for k, v in data.items() if k in known})

    def save(self, state: DomainState) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self.path_for(state.domain).write_text(json.dumps(asdict(state), indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/state.py tests/test_state.py
git commit -m "feat: add per-domain state store"
```

---

### Task 5a: Namecheap provider (nameserver read + set)

**Files:**
- Create: `src/wire_domain/providers/__init__.py` (empty)
- Create: `src/wire_domain/providers/namecheap.py`
- Test: `tests/test_namecheap.py`

**Interfaces:**
- Consumes: `Settings` (Task 3), `NamecheapError` (Task 2).
- Produces:
  - `nameservers_equal(a: Iterable[str], b: Iterable[str]) -> bool` — case-insensitive, order-independent, trailing-dot-insensitive compare.
  - `class NamecheapProvider`:
    - `__init__(self, settings: Settings, client: object | None = None)` — builds a real `namecheap.NamecheapClient` when `client is None`, else uses the injected fake (tests). Store as `self.client`.
    - `get_nameservers(self, domain: str) -> list[str]` — calls `self.client.domains.dns.get_list(domain)`, returns `Nameservers` list; wraps failures in `NamecheapError`.
    - `set_nameservers(self, domain: str, nameservers: list[str]) -> None` — calls `self.client.domains.dns.set_custom(domain, nameservers)`; raises `NamecheapError` if the result's `IsSuccess` is falsy or the call throws.

- [ ] **Step 1: Write the failing test**

`tests/test_namecheap.py`:
```python
import pytest

from wire_domain.config import Settings
from wire_domain.errors import NamecheapError
from wire_domain.providers.namecheap import NamecheapProvider, nameservers_equal


def make_settings() -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=None,
        vercel_token="v", vercel_team_id=None, vercel_project=None,
    )


class FakeDns:
    def __init__(self, ns, set_result=None, raise_on_set=False):
        self._ns = ns
        self._set_result = set_result or {"IsSuccess": True}
        self._raise_on_set = raise_on_set
        self.set_calls = []

    def get_list(self, domain):
        return {"Domain": domain, "IsUsingOurDNS": False, "Nameservers": self._ns}

    def set_custom(self, domain, nameservers):
        if self._raise_on_set:
            raise RuntimeError("api down")
        self.set_calls.append((domain, nameservers))
        return self._set_result


class FakeClient:
    def __init__(self, dns):
        self.domains = type("D", (), {"dns": dns})()


def test_nameservers_equal_normalizes():
    assert nameservers_equal(["NS1.CF.com", "ns2.cf.com."], ["ns2.cf.com", "ns1.cf.com"])
    assert not nameservers_equal(["ns1.cf.com"], ["ns1.cf.com", "ns2.cf.com"])


def test_get_nameservers():
    dns = FakeDns(ns=["dns1.registrar-servers.com", "dns2.registrar-servers.com"])
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    assert p.get_nameservers("example.com") == [
        "dns1.registrar-servers.com",
        "dns2.registrar-servers.com",
    ]


def test_set_nameservers_success():
    dns = FakeDns(ns=[], set_result={"IsSuccess": True})
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])
    assert dns.set_calls == [("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])]


def test_set_nameservers_unsuccessful_raises():
    dns = FakeDns(ns=[], set_result={"IsSuccess": False, "Warnings": "nope"})
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    with pytest.raises(NamecheapError):
        p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])


def test_set_nameservers_exception_wrapped():
    dns = FakeDns(ns=[], raise_on_set=True)
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    with pytest.raises(NamecheapError):
        p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_namecheap.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/providers/__init__.py`: empty file.

`src/wire_domain/providers/namecheap.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_namecheap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/providers/__init__.py src/wire_domain/providers/namecheap.py tests/test_namecheap.py
git commit -m "feat: add namecheap nameserver provider"
```

---

### Task 5b: Cloudflare provider (zone get-or-create + ensure records)

**Files:**
- Create: `src/wire_domain/providers/cloudflare.py`
- Test: `tests/test_cloudflare.py`

**Interfaces:**
- Consumes: `Settings` (Task 3), `RecordSpec` + `StepStatus` (Task 2), `CloudflareProviderError` (Task 2).
- Produces:
  - `@dataclass ZoneInfo(id: str, name: str, name_servers: list[str], status: str, created: bool)`.
  - `class CloudflareProvider`:
    - `__init__(self, settings: Settings, client: object | None = None)` — builds real `cloudflare.Cloudflare(api_token=...)` if `client is None`. Stores `self.settings`, `self.client`.
    - `get_or_create_zone(self, domain: str) -> ZoneInfo` — `self.client.zones.list(name=domain)`; if a zone with matching name exists reuse it (`created=False`); else resolve account id (`self._account_id()`) and `self.client.zones.create(account={"id": account_id}, name=domain, type="full")` (`created=True`). Extract `id`, `name`, `name_servers` (default `[]`), `status`.
    - `ensure_record(self, zone_id: str, spec: RecordSpec) -> StepStatus` — list records via `self.client.dns.records.list(zone_id=zone_id, type=spec.type, name=self._fqdn(zone_name, spec.name))` — but since `zone_name` is needed, accept the fqdn precomputed: signature is `ensure_record(self, zone_id: str, zone_name: str, spec: RecordSpec)`. Compute fqdn (`zone_name` for `@`, else `f"{spec.name}.{zone_name}"`). If a record of that type+name exists and content/proxied match → return `"skipped"`; if it exists but differs → `update(...)` → `"updated"`; if none → `create(...)` → `"created"`. Wrap failures in `CloudflareProviderError`.
    - `_account_id(self) -> str` — return `settings.cloudflare_account_id` if set; else `self.client.accounts.list()`, take the single account's `id`; raise `CloudflareProviderError` if zero or more-than-one and none configured.
    - `_fqdn(zone_name: str, name: str) -> str` staticmethod.

- [ ] **Step 1: Write the failing test**

`tests/test_cloudflare.py`:
```python
import pytest

from wire_domain.config import Settings
from wire_domain.errors import CloudflareProviderError
from wire_domain.models import RecordSpec
from wire_domain.providers.cloudflare import CloudflareProvider, ZoneInfo


def make_settings(account_id=None) -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=account_id,
        vercel_token="v", vercel_team_id=None, vercel_project=None,
    )


class Obj:
    """Attribute bag mimicking SDK response objects."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeZones:
    def __init__(self, existing=None):
        self._existing = existing or []
        self.created = []

    def list(self, name=None):
        return [z for z in self._existing if z.name == name]

    def create(self, account=None, name=None, type=None):
        zone = Obj(id="new-zone", name=name, name_servers=["ns1.cf.com", "ns2.cf.com"], status="pending")
        self.created.append((account, name, type))
        return zone


class FakeRecords:
    def __init__(self, existing=None):
        self._existing = existing or []  # list of Obj(id,type,name,content,proxied)
        self.created = []
        self.updated = []

    def list(self, zone_id=None, type=None, name=None):
        return [r for r in self._existing if r.type == type and r.name == name]

    def create(self, zone_id=None, type=None, name=None, content=None, ttl=None, proxied=None):
        self.created.append(dict(type=type, name=name, content=content, proxied=proxied))
        return Obj(id="rec-new")

    def update(self, dns_record_id, zone_id=None, type=None, name=None, content=None, ttl=None, proxied=None):
        self.updated.append(dict(id=dns_record_id, content=content, proxied=proxied))
        return Obj(id=dns_record_id)


class FakeAccounts:
    def __init__(self, accounts):
        self._accounts = accounts

    def list(self):
        return self._accounts


class FakeCF:
    def __init__(self, zones, records, accounts=None):
        self.zones = zones
        self.dns = Obj(records=records)
        self.accounts = accounts or FakeAccounts([Obj(id="acct-1")])


def test_get_or_create_zone_reuses_existing():
    existing = Obj(id="z1", name="example.com", name_servers=["a.ns", "b.ns"], status="active")
    cf = FakeCF(FakeZones(existing=[existing]), FakeRecords())
    p = CloudflareProvider(make_settings(), client=cf)
    info = p.get_or_create_zone("example.com")
    assert isinstance(info, ZoneInfo)
    assert info.id == "z1"
    assert info.created is False
    assert info.name_servers == ["a.ns", "b.ns"]


def test_get_or_create_zone_creates_with_account():
    cf = FakeCF(FakeZones(existing=[]), FakeRecords())
    p = CloudflareProvider(make_settings(account_id="acct-explicit"), client=cf)
    info = p.get_or_create_zone("example.com")
    assert info.id == "new-zone"
    assert info.created is True
    assert cf.zones.created[0][0] == {"id": "acct-explicit"}


def test_zone_create_ambiguous_account_raises():
    cf = FakeCF(FakeZones(existing=[]), FakeRecords(), accounts=FakeAccounts([Obj(id="a"), Obj(id="b")]))
    p = CloudflareProvider(make_settings(), client=cf)  # no explicit account id
    with pytest.raises(CloudflareProviderError):
        p.get_or_create_zone("example.com")


def test_ensure_record_creates_when_missing():
    cf = FakeCF(FakeZones(), FakeRecords(existing=[]))
    p = CloudflareProvider(make_settings(), client=cf)
    spec = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
    status = p.ensure_record("z1", "example.com", spec)
    assert status == "created"
    assert cf.dns.records.created[0]["name"] == "example.com"
    assert cf.dns.records.created[0]["content"] == "76.76.21.21"


def test_ensure_record_skips_when_correct():
    existing = Obj(id="r1", type="A", name="example.com", content="76.76.21.21", proxied=False)
    cf = FakeCF(FakeZones(), FakeRecords(existing=[existing]))
    p = CloudflareProvider(make_settings(), client=cf)
    spec = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
    assert p.ensure_record("z1", "example.com", spec) == "skipped"
    assert cf.dns.records.created == []
    assert cf.dns.records.updated == []


def test_ensure_record_updates_when_content_differs():
    existing = Obj(id="r1", type="A", name="example.com", content="1.1.1.1", proxied=False)
    cf = FakeCF(FakeZones(), FakeRecords(existing=[existing]))
    p = CloudflareProvider(make_settings(), client=cf)
    spec = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
    assert p.ensure_record("z1", "example.com", spec) == "updated"
    assert cf.dns.records.updated[0]["id"] == "r1"
    assert cf.dns.records.updated[0]["content"] == "76.76.21.21"


def test_ensure_record_cname_www_fqdn():
    cf = FakeCF(FakeZones(), FakeRecords(existing=[]))
    p = CloudflareProvider(make_settings(), client=cf)
    spec = RecordSpec(type="CNAME", name="www", content="cname.vercel-dns.com", proxied=False)
    p.ensure_record("z1", "example.com", spec)
    assert cf.dns.records.created[0]["name"] == "www.example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cloudflare.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/providers/cloudflare.py`:
```python
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
                raise CloudflareProviderError(f"Failed to create zone for {domain}", cause=exc) from exc
            created = True

        return ZoneInfo(
            id=zone.id,
            name=zone.name,
            name_servers=list(getattr(zone, "name_servers", None) or []),
            status=getattr(zone, "status", "unknown"),
            created=created,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cloudflare.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/providers/cloudflare.py tests/test_cloudflare.py
git commit -m "feat: add cloudflare zone and record provider"
```

---

### Task 5c: Vercel provider (list + add project domains via httpx)

**Files:**
- Create: `src/wire_domain/providers/vercel.py`
- Test: `tests/test_vercel.py`

**Interfaces:**
- Consumes: `Settings` (Task 3), `VercelError` (Task 2).
- Produces:
  - `class VercelProvider`:
    - `__init__(self, settings: Settings, project: str, transport: httpx.BaseTransport | None = None)` — builds an `httpx.Client(base_url="https://api.vercel.com", headers={"Authorization": f"Bearer {token}"}, transport=transport)`. Store `self.project`, `self.team_id`, `self.client`.
    - `_params(self) -> dict` — `{"teamId": self.team_id}` if set else `{}`.
    - `list_domains(self) -> list[str]` — `GET /v9/projects/{project}/domains`; return the `name` of each entry. Raise `VercelError` on non-2xx.
    - `add_domain(self, name: str) -> StepStatus` — if `name` already in `list_domains()` → `"skipped"`. Else `POST /v10/projects/{project}/domains` with json `{"name": name}`. On 2xx → `"created"`. On 409 → re-check `list_domains()`; if present now → `"skipped"`, else raise `VercelError` (domain in use elsewhere). Other non-2xx → `VercelError`.
    - `close(self) -> None` — closes the httpx client.

- [ ] **Step 1: Write the failing test**

`tests/test_vercel.py`:
```python
import httpx
import pytest

from wire_domain.config import Settings
from wire_domain.errors import VercelError
from wire_domain.providers.vercel import VercelProvider


def make_settings(team_id=None) -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=None,
        vercel_token="vtoken", vercel_team_id=team_id, vercel_project=None,
    )


def make_provider(handler, project="proj", team_id=None) -> VercelProvider:
    return VercelProvider(make_settings(team_id=team_id), project=project,
                          transport=httpx.MockTransport(handler))


def test_list_domains():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v9/projects/proj/domains"
        return httpx.Response(200, json={"domains": [{"name": "example.com"}, {"name": "www.example.com"}]})

    p = make_provider(handler)
    assert p.list_domains() == ["example.com", "www.example.com"]


def test_add_domain_creates():
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"domains": []})
        calls["post"] += 1
        assert request.method == "POST"
        assert request.url.path == "/v10/projects/proj/domains"
        return httpx.Response(200, json={"name": "example.com"})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "created"
    assert calls["post"] == 1


def test_add_domain_skips_when_present():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # never POSTs
        return httpx.Response(200, json={"domains": [{"name": "example.com"}]})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "skipped"


def test_add_domain_409_but_now_present_is_skipped():
    state = {"added": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            names = [{"name": "example.com"}] if state["added"] else []
            return httpx.Response(200, json={"domains": names})
        state["added"] = True  # someone else added it concurrently
        return httpx.Response(409, json={"error": {"code": "domain_already_in_use"}})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "skipped"


def test_add_domain_409_elsewhere_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"domains": []})
        return httpx.Response(409, json={"error": {"code": "domain_already_in_use"}})

    p = make_provider(handler)
    with pytest.raises(VercelError):
        p.add_domain("example.com")


def test_team_id_in_query():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("teamId") == "team_x"
        return httpx.Response(200, json={"domains": []})

    p = make_provider(handler, team_id="team_x")
    assert p.list_domains() == []


def test_auth_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "forbidden"}})

    p = make_provider(handler)
    with pytest.raises(VercelError):
        p.list_domains()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vercel.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/providers/vercel.py`:
```python
"""Vercel provider: list and add project domains via the REST API (httpx)."""

from __future__ import annotations

import httpx

from wire_domain.config import Settings
from wire_domain.errors import VercelError
from wire_domain.models import StepStatus

_BASE_URL = "https://api.vercel.com"


class VercelProvider:
    def __init__(
        self,
        settings: Settings,
        project: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.project = project
        self.team_id = settings.vercel_team_id
        self.client = httpx.Client(
            base_url=_BASE_URL,
            headers={"Authorization": f"Bearer {settings.vercel_token}"},
            transport=transport,
            timeout=30.0,
        )

    def _params(self) -> dict:
        return {"teamId": self.team_id} if self.team_id else {}

    def list_domains(self) -> list[str]:
        resp = self.client.get(f"/v9/projects/{self.project}/domains", params=self._params())
        if resp.status_code >= 400:
            raise VercelError(f"Failed to list Vercel domains ({resp.status_code}): {resp.text}")
        return [d["name"] for d in resp.json().get("domains", [])]

    def add_domain(self, name: str) -> StepStatus:
        if name in self.list_domains():
            return "skipped"

        resp = self.client.post(
            f"/v10/projects/{self.project}/domains",
            params=self._params(),
            json={"name": name},
        )
        if resp.status_code < 300:
            return "created"
        if resp.status_code == 409:
            if name in self.list_domains():
                return "skipped"
            raise VercelError(f"Domain {name} is already in use by another Vercel project.")
        raise VercelError(f"Failed to add Vercel domain {name} ({resp.status_code}): {resp.text}")

    def close(self) -> None:
        self.client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_vercel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/providers/vercel.py tests/test_vercel.py
git commit -m "feat: add vercel domain provider"
```

---

### Task 6: Orchestrator + report rendering (flow.py)

**Files:**
- Create: `src/wire_domain/flow.py`
- Test: `tests/test_flow.py`

**Interfaces:**
- Consumes: `Settings`, `StateStore`/`DomainState`, `NamecheapProvider`+`nameservers_equal`, `CloudflareProvider`, `VercelProvider`, `RecordSpec`, `StepResult`, `WireReport`, all provider errors.
- Produces:
  - `vercel_record_specs(include_www: bool) -> list[RecordSpec]` — returns the A `@` spec, plus the `www` CNAME spec when `include_www`.
  - `@dataclass WirePlan(domain: str, project: str, include_www: bool, dry_run: bool)`.
  - `class Orchestrator`:
    - `__init__(self, settings, state_store, namecheap, cloudflare, vercel_factory)` — `vercel_factory: Callable[[str], VercelProvider]` builds a `VercelProvider` for a given project (lets `wire()` construct it only after the project is known, and lets tests inject fakes).
    - `wire(self, plan: WirePlan) -> WireReport` — runs the 4 steps in order, each appending a `StepResult`; persists `DomainState` after zone + ns steps; on `dry_run` performs no writes but still reads live state to compute intended statuses; returns the report. A `WireError` in any step is caught, recorded as a `failed` StepResult with the message, and stops subsequent steps.
    - `status(self, domain: str, project: str, include_www: bool) -> WireReport` — read-only; reports zone existence/status, whether NS match, record correctness (via a live list — reuse `CloudflareProvider.ensure_record` is a write path, so add read-only checks here), and Vercel attachment. Never writes.
  - `render_report(report: WireReport, note: str | None = None) -> None` — prints a rich table (Step, Status with color, Detail) plus optional note line.

  Status→color map for rendering: `created`=green, `updated`=yellow, `skipped`=dim, `pending`=cyan, `failed`=red.

- [ ] **Step 1: Write the failing test**

`tests/test_flow.py`:
```python
import pytest

from wire_domain.config import Settings
from wire_domain.flow import Orchestrator, WirePlan, vercel_record_specs
from wire_domain.models import RecordSpec
from wire_domain.state import StateStore


def make_settings() -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=None,
        vercel_token="v", vercel_team_id=None, vercel_project=None,
    )


class FakeZoneInfo:
    def __init__(self, created):
        self.id = "z1"
        self.name = "example.com"
        self.name_servers = ["ns1.cloudflare.com", "ns2.cloudflare.com"]
        self.status = "pending"
        self.created = created


class FakeCloudflare:
    def __init__(self, zone_created=True):
        self._zone_created = zone_created
        self.records = []

    def get_or_create_zone(self, domain):
        return FakeZoneInfo(self._zone_created)

    def ensure_record(self, zone_id, zone_name, spec: RecordSpec):
        self.records.append(spec)
        return "created"


class FakeNamecheap:
    def __init__(self, current_ns):
        self._ns = current_ns
        self.set_calls = []

    def get_nameservers(self, domain):
        return self._ns

    def set_nameservers(self, domain, nameservers):
        self.set_calls.append(nameservers)


class FakeVercel:
    def __init__(self):
        self.added = []

    def add_domain(self, name):
        self.added.append(name)
        return "created"

    def close(self):
        pass


def build(tmp_path, namecheap, cloudflare, vercel):
    return Orchestrator(
        settings=make_settings(),
        state_store=StateStore(tmp_path),
        namecheap=namecheap,
        cloudflare=cloudflare,
        vercel_factory=lambda project: vercel,
    )


def test_vercel_record_specs():
    specs = vercel_record_specs(include_www=True)
    assert [(s.type, s.name, s.content, s.proxied) for s in specs] == [
        ("A", "@", "76.76.21.21", False),
        ("CNAME", "www", "cname.vercel-dns.com", False),
    ]
    assert len(vercel_record_specs(include_www=False)) == 1


def test_wire_full_flow_sets_ns_and_records_and_vercel(tmp_path):
    nc = FakeNamecheap(current_ns=["dns1.registrar-servers.com", "dns2.registrar-servers.com"])
    cf = FakeCloudflare(zone_created=True)
    vc = FakeVercel()
    orch = build(tmp_path, nc, cf, vc)
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=False))

    assert report.ok is True
    # nameservers were different -> set called with cloudflare ns
    assert nc.set_calls == [["ns1.cloudflare.com", "ns2.cloudflare.com"]]
    # two records ensured
    assert len(cf.records) == 2
    # apex + www added to vercel
    assert vc.added == ["example.com", "www.example.com"]
    # state persisted
    saved = StateStore(tmp_path).load("example.com")
    assert saved.zone_id == "z1"
    assert saved.cloudflare_nameservers == ["ns1.cloudflare.com", "ns2.cloudflare.com"]


def test_wire_skips_ns_when_already_set(tmp_path):
    nc = FakeNamecheap(current_ns=["ns1.cloudflare.com", "ns2.cloudflare.com"])
    orch = build(tmp_path, nc, FakeCloudflare(zone_created=False), FakeVercel())
    report = orch.wire(WirePlan("example.com", "proj", include_www=False, dry_run=False))
    assert nc.set_calls == []
    ns_step = next(s for s in report.steps if s.name == "namecheap-nameservers")
    assert ns_step.status == "skipped"


def test_wire_dry_run_performs_no_writes(tmp_path):
    nc = FakeNamecheap(current_ns=["dns1.registrar-servers.com"])
    cf = FakeCloudflare()
    vc = FakeVercel()
    orch = build(tmp_path, nc, cf, vc)
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=True))
    assert nc.set_calls == []
    assert cf.records == []
    assert vc.added == []
    assert report.ok is True


def test_wire_failure_stops_and_marks_failed(tmp_path):
    from wire_domain.errors import CloudflareProviderError

    class BoomCloudflare(FakeCloudflare):
        def get_or_create_zone(self, domain):
            raise CloudflareProviderError("zone api down")

    orch = build(tmp_path, FakeNamecheap([]), BoomCloudflare(), FakeVercel())
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=False))
    assert report.ok is False
    assert report.steps[0].status == "failed"
    # flow stopped: only the zone step recorded
    assert len(report.steps) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flow.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/flow.py`:
```python
"""Orchestrate the wire flow across the three providers, idempotently."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wire_domain.config import Settings
from wire_domain.console import console
from wire_domain.errors import WireError
from wire_domain.models import RecordSpec, StepResult, WireReport
from wire_domain.providers.cloudflare import CloudflareProvider
from wire_domain.providers.namecheap import NamecheapProvider, nameservers_equal
from wire_domain.providers.vercel import VercelProvider
from wire_domain.state import StateStore

_APEX = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
_WWW = RecordSpec(type="CNAME", name="www", content="cname.vercel-dns.com", proxied=False)

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
                from datetime import datetime, timezone

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
        # Uses provider list without creating. Returns ZoneInfo-like or None.
        try:
            zones = [z for z in self.cloudflare.client.zones.list(name=domain) if z.name == domain]
        except Exception as exc:  # noqa: BLE001
            raise WireError(f"Failed to read zone for {domain}", cause=exc) from exc
        if not zones:
            return None
        z = zones[0]
        from wire_domain.providers.cloudflare import ZoneInfo

        return ZoneInfo(id=z.id, name=z.name, name_servers=list(getattr(z, "name_servers", None) or []),
                        status=getattr(z, "status", "unknown"), created=False)


def _failed_step_name(exc: WireError) -> str:
    from wire_domain.errors import (
        CloudflareProviderError,
        NamecheapError,
        VercelError,
    )

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_flow.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/flow.py tests/test_flow.py
git commit -m "feat: add orchestrator and report rendering"
```

---

### Task 7: Typer CLI wiring (`wire` + `status` commands, exit codes)

**Files:**
- Create: `src/wire_domain/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `app = typer.Typer(...)`.
  - `build_orchestrator(settings: Settings, state_dir: Path) -> Orchestrator` — constructs the three real providers and a `vercel_factory`. Kept module-level so tests can monkeypatch it.
  - `@app.command() wire(domain, project, www/no_www, dry_run, yes, state_dir, verbose)` — loads settings (ConfigError→exit 1), resolves project (`--project` or `settings.vercel_project`; missing→exit 1 with message), optional confirm unless `--yes`/`--dry-run`, runs `Orchestrator.wire`, renders report + propagation note, exits `0` if `report.ok` else `2`.
  - `@app.command() status(domain, project, www/no_www, state_dir, verbose)` — read-only; renders `Orchestrator.status`; exit 0 (or 1 on ConfigError).
  - Uncaught `WireError` at the top level prints a clean rich error panel (or full traceback when `--verbose`) and exits with the mapped code.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

import wire_domain.cli as cli_mod
from wire_domain.cli import app
from wire_domain.errors import ConfigError
from wire_domain.models import StepResult, WireReport

runner = CliRunner()

ENV = {
    "NAMECHEAP_API_USER": "u", "NAMECHEAP_API_KEY": "k", "NAMECHEAP_USERNAME": "u",
    "NAMECHEAP_CLIENT_IP": "1.2.3.4", "CLOUDFLARE_API_TOKEN": "c", "VERCEL_TOKEN": "v",
}


class FakeOrch:
    def __init__(self, ok=True):
        self._ok = ok

    def wire(self, plan):
        r = WireReport(domain=plan.domain)
        r.add(StepResult("cloudflare-zone", "created" if self._ok else "failed"))
        return r

    def status(self, domain, project, include_www):
        r = WireReport(domain=domain)
        r.add(StepResult("cloudflare-zone", "skipped"))
        return r


def patch_env(monkeypatch, env):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: cli_mod.load_settings.__wrapped__(env=env)
                        if False else _settings_from(env))


def _settings_from(env):
    from wire_domain.config import load_settings
    return load_settings(env=env)


def test_wire_success_exit_0(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch(ok=True))
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_wire_step_failure_exit_2(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch(ok=False))
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 2


def test_wire_config_error_exit_1(monkeypatch, tmp_path):
    def boom(**kw):
        raise ConfigError("Missing required configuration: VERCEL_TOKEN")
    monkeypatch.setattr(cli_mod, "load_settings", boom)
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Missing required configuration" in result.output


def test_wire_missing_project_exit_1(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from(ENV))  # no VERCEL_PROJECT
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch())
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "project" in result.output.lower()


def test_status_exit_0(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch())
    result = runner.invoke(app, ["status", "example.com", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the implementation**

`src/wire_domain/cli.py`:
```python
"""Typer CLI for wire-domain."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from wire_domain.config import load_settings, render_config_table, Settings
from wire_domain.console import console, err_console
from wire_domain.errors import (
    CloudflareProviderError,
    ConfigError,
    NamecheapError,
    VercelError,
    WireError,
)
from wire_domain.flow import Orchestrator, WirePlan, render_report
from wire_domain.providers.cloudflare import CloudflareProvider
from wire_domain.providers.namecheap import NamecheapProvider
from wire_domain.providers.vercel import VercelProvider
from wire_domain.state import StateStore, default_state_dir

app = typer.Typer(help="Wire an already-registered domain: Namecheap -> Cloudflare -> Vercel.")

_PROPAGATION_NOTE = (
    "Nameserver changes can take minutes to 48 hours to propagate. "
    "The Cloudflare zone stays 'pending' until propagation completes."
)

_EXIT_FOR = {ConfigError: 1}


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
    if verbose and exc.cause is not None:
        err_console.print_exception()
    err_console.print(f"[red]error:[/red] {exc}")


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

        if not yes and not dry_run:
            typer.confirm("Proceed?", abort=True)

        orch = build_orchestrator(settings, state_dir)
        report = orch.wire(WirePlan(domain=domain, project=resolved_project, include_www=www, dry_run=dry_run))
        render_report(report, note=_PROPAGATION_NOTE)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire_domain/cli.py tests/test_cli.py
git commit -m "feat: add typer cli with wire and status commands"
```

---

### Task 8: Full-suite green + entry point smoke

**Files:**
- Modify: none (verification task); may fix any cross-module issues found.
- Test: all existing tests.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -v`
Expected: ALL PASS.

- [ ] **Step 2: Verify the console entry point loads**

Run: `uv run wire-domain --help`
Expected: Typer help text listing `wire` and `status`. (No network calls.)

- [ ] **Step 3: Verify module invocation**

Run: `uv run python -m wire_domain --help`
Expected: same help text.

- [ ] **Step 4: Verify config-error path end-to-end (no secrets set)**

Run: `env -i "PATH=$PATH" uv run wire-domain wire example.com --yes 2>&1 | head -5 || true`
Expected: prints "Missing required configuration: ..." and exits non-zero (code 1). (Confirms real `load_settings` wiring.)

- [ ] **Step 5: Commit (if any fixes were needed)**

```bash
git add -A
git commit -m "test: verify full suite and entry points green" || echo "nothing to commit"
```

---

### Task 9: README usage docs

**Files:**
- Modify: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Expand the README**

Replace `README.md` with full documentation covering: what it does (Namecheap → Cloudflare → Vercel), prerequisites (domain already registered in Namecheap; Namecheap API access enabled with the client IP whitelisted; Cloudflare API token with Zone:Edit + DNS:Edit; Vercel token + project), install via `uv sync`, `.env` setup (reference `.env.example`), usage for `wire` (all flags: `--project`, `--www/--no-www`, `--dry-run`, `--yes`, `--state-dir`, `--verbose`) and `status`, the idempotency guarantee, the nameserver-propagation caveat, and exit codes (0/1/2). Use real command examples:

```markdown
## Prerequisites
- The domain is already registered in your Namecheap account.
- Namecheap API access enabled and your public IP whitelisted (`NAMECHEAP_CLIENT_IP`).
- A Cloudflare API token with **Zone:Edit** and **DNS:Edit**.
- A Vercel token and target project.

## Configure
Copy `.env.example` to `.env` and fill in the values.

## Commands
```bash
# Full wire (asks for confirmation first)
uv run wire-domain wire example.com --project my-project

# Preview without changing anything
uv run wire-domain wire example.com --project my-project --dry-run

# Non-interactive
uv run wire-domain wire example.com --project my-project --yes

# Apex only, no www
uv run wire-domain wire example.com --project my-project --no-www

# Read-only status across all three providers
uv run wire-domain status example.com --project my-project
```

## Exit codes
- `0` success · `1` configuration error · `2` a step failed
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: full wire-domain usage guide"
```

---

## Self-Review

**Spec coverage:**
- Core flow steps 2–5 (zone, nameservers, records, Vercel, report): Tasks 5a/5b/5c/6/7. ✓
- Step 1 (registration): intentionally out of scope per approved spec. ✓
- uv + pyproject `src/` package: Task 1. ✓
- Idempotency via live detection + state file: Tasks 4, 5b, 5c, 6. ✓
- `.env` + rich validation table: Task 3. ✓
- Commands `wire` + read-only `status`: Task 7. ✓
- Error handling, exit codes, `--dry-run`, `--verbose`, masking: Tasks 2, 3, 6, 7. ✓
- Testing strategy (MockTransport, mocked SDKs): every provider/flow/cli task. ✓
- Cloudflare account-id resolution for zone create: Task 5b. ✓

**Placeholder scan:** No "TBD"/"handle edge cases". The one deliberate call-out is the `flow.py` clean-up note in Task 6, which gives the exact corrected code — not a placeholder.

**Type consistency:** `StepStatus` literals, `RecordSpec`, `WireReport.add/ok`, `ZoneInfo`, `Settings` field names, `Orchestrator(vercel_factory=...)`, and provider method signatures are used identically across Tasks 2→7. `nameservers_equal` defined in 5a, used in 6. `build_orchestrator`/`load_settings` monkeypatch seams match Task 7 tests.

**Scope:** Single implementation plan, one cohesive CLI. No decomposition needed.
