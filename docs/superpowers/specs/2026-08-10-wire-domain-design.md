# wire-domain — Design

**Date:** 2026-08-10
**Status:** Approved (pending spec review)

## Purpose

A production-ready CLI that wires an **already-registered** domain through the
chain **Namecheap (registrar) → Cloudflare (authoritative DNS) → Vercel
(hosting)**. Domain registration is explicitly out of scope; the domain is
assumed to already exist in the Namecheap account.

Given a domain and a Vercel project, the tool:

1. Creates (or reuses) a Cloudflare zone for the domain.
2. Points the domain's nameservers at Cloudflare via Namecheap.
3. Creates the DNS records Vercel needs (apex A record + `www` CNAME).
4. Adds the domain (and optionally `www`) to a Vercel project.
5. Prints a clear final status report.

Every step is **idempotent** and safe to re-run: it detects existing state at
each provider and skips, updates, or creates as appropriate.

## Tech Stack (fixed)

- Python 3.11+
- [Typer](https://typer.tiangolo.com/) — CLI framework
- [rich](https://rich.readthedocs.io/) — terminal output
- [python-dotenv](https://pypi.org/project/python-dotenv/) — `.env` loading
- [namecheap-python](https://github.com/adriangalilea/namecheap-python) — registrar API
- Official [cloudflare](https://github.com/cloudflare/cloudflare-python) Python SDK
- [httpx](https://www.python-httpx.org/) — Vercel REST API
- Packaging & workflow: **uv** + `pyproject.toml`, `src/` layout

## Package Layout

```
wire-domain/
├── pyproject.toml            # deps + [project.scripts] wire-domain = "wire_domain.cli:app"
├── README.md
├── .env.example
├── .gitignore
├── src/wire_domain/
│   ├── __init__.py           # __version__
│   ├── __main__.py           # enables `python -m wire_domain`
│   ├── cli.py                # Typer app: `wire`, `status`
│   ├── config.py            # load .env, validate, rich table of missing vars
│   ├── console.py            # shared rich Console
│   ├── state.py              # per-domain resumable JSON state
│   ├── models.py             # StepResult, WireReport, Settings dataclasses
│   ├── errors.py             # WireError + provider-tagged subclasses
│   ├── flow.py               # orchestrator (idempotent step runner)
│   └── providers/
│       ├── __init__.py
│       ├── namecheap.py      # NS read + set-custom
│       ├── cloudflare.py     # get-or-create zone, ensure DNS records
│       └── vercel.py         # add domain to project (httpx REST)
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_state.py
    ├── test_namecheap.py
    ├── test_cloudflare.py
    ├── test_vercel.py
    └── test_flow.py
```

## Commands

### `wire-domain wire <domain>`
The main orchestrated flow.

| Option | Default | Meaning |
|--------|---------|---------|
| `--project` | `VERCEL_PROJECT` env | Vercel project name/ID to attach the domain to (required if env unset). |
| `--www / --no-www` | `--www` | Also create the `www` CNAME and add `www.<domain>` to Vercel. |
| `--dry-run` | off | Print intended mutations without executing any of them. |
| `--yes` | off | Skip the pre-flight summary confirmation prompt. |
| `--state-dir` | `~/.wire-domain/state` | Where to read/write the per-domain state file. |
| `--verbose` | off | Full rich tracebacks and provider request detail. |

### `wire-domain status <domain>`
**Read-only.** Reports the live state across all three providers without
mutating anything: whether the Cloudflare zone exists (and its activation
status), the domain's current nameservers at Namecheap (and whether they match
Cloudflare's), the presence/correctness of the DNS records, and whether the
domain/`www` are attached to the Vercel project.

## Flow (idempotent steps)

Idempotency is achieved primarily by **live detection** — each step queries the
provider for current state before mutating.

1. **Cloudflare — get-or-create zone.** List zones filtered by name. If the
   zone exists, reuse its `id` and read its assigned nameservers. Otherwise
   create the zone. Output: `zone_id`, `cloudflare_nameservers`, `zone_status`.

2. **Namecheap — set nameservers.** Split the domain into SLD + TLD. Read
   current nameservers. If they already equal the Cloudflare set, skip.
   Otherwise call set-custom-nameservers with the Cloudflare nameservers and
   record the change timestamp (starts the propagation clock).

3. **Cloudflare — ensure DNS records.** For each desired record, list existing
   records of that type/name:
   - `A` `@` → `76.76.21.21`, `proxied=false`
   - `CNAME` `www` → `cname.vercel-dns.com`, `proxied=false` (only when `--www`)

   Create if missing; patch if present but content/proxied differs; skip if
   already correct.

4. **Vercel — attach domain.** Query the project's existing domains. Add the
   apex domain (and `www.<domain>` when `--www`) via
   `POST /v10/projects/{project}/domains`. Treat an "already exists" (HTTP 409 /
   `domain_already_in_use` on same project) response as success.

5. **Report.** Render a rich table with one row per step and its outcome
   (`created` / `skipped` / `updated` / `failed`), plus a note that nameserver
   propagation can take hours and the Cloudflare zone remains `pending` until it
   completes. `status <domain>` re-uses the same rendering.

## Idempotency & State

- **Primary mechanism:** live detection at each provider (authoritative).
- **State file:** `~/.wire-domain/state/<domain>.json` (override via
  `--state-dir`). Records `zone_id`, `cloudflare_nameservers`,
  `nameservers_changed_at`, `last_completed_step`, and `updated_at`.
- The state file is a **cache and audit trail only** — it is used for the
  propagation note and resumable reporting. If it conflicts with live provider
  state, live state wins and the file is refreshed. Deleting it never changes
  correctness.

## Configuration & Secrets

Loaded from `.env` via python-dotenv (real environment variables take
precedence over the file). `config.py` validates all required variables up
front and prints a rich table showing each variable, whether it is present, and
a masked preview of its value. If anything required is missing it exits with
code `1` and a clear message before any provider is contacted.

| Variable | Required | Purpose |
|----------|----------|---------|
| `NAMECHEAP_API_USER` | yes | Namecheap API user. |
| `NAMECHEAP_API_KEY` | yes | Namecheap API key. |
| `NAMECHEAP_USERNAME` | yes | Namecheap account username. |
| `NAMECHEAP_CLIENT_IP` | yes | Whitelisted client IP for the Namecheap API. |
| `NAMECHEAP_SANDBOX` | no (default `false`) | Use the Namecheap sandbox endpoint. |
| `CLOUDFLARE_API_TOKEN` | yes | Scoped token with Zone:Edit + DNS:Edit. |
| `VERCEL_TOKEN` | yes | Vercel access token. |
| `VERCEL_TEAM_ID` | no | Team/scope ID for team projects. |
| `VERCEL_PROJECT` | no | Default Vercel project when `--project` is omitted. |

## Error Handling & UX

- Custom exception hierarchy: `WireError` base, with `NamecheapError`,
  `CloudflareProviderError`, `VercelError`, and `ConfigError` subclasses, each
  carrying provider context and the underlying cause.
- `--dry-run` prints every intended mutation (with the resolved payload) and
  performs no writes.
- A pre-flight summary (domain, target project, records to be created,
  nameservers to be set) is shown before mutations begin; `--yes` skips it.
- Exit codes: `0` success, `1` configuration error, `2` step failure.
- Tracebacks are suppressed by default (clean rich error panel); `--verbose`
  restores full tracebacks and request detail.

## Testing Strategy

pytest, no real network calls.

- **Vercel:** `httpx.MockTransport` to simulate REST responses (add success,
  409 already-exists, auth failure).
- **Cloudflare & Namecheap:** monkeypatched/mocked SDK client objects returning
  canned responses.
- **Coverage targets:**
  - `config`: missing-var detection and masking.
  - `state`: read/write round-trip, `--state-dir` override, missing-file
    tolerance.
  - Providers: zone reuse vs. create, NS already-set skip, DNS record
    create/patch/skip, Vercel add vs. already-exists.
  - `flow`: correct step ordering, dry-run performs no writes, a mid-flow
    failure surfaces as exit code `2` with the report showing completed steps.

## Out of Scope

- Domain registration or availability purchase (domains are pre-acquired).
- Non-Vercel hosting targets or record sets beyond the apex A + `www` CNAME.
- Automatic waiting/polling for nameserver propagation (reported, not awaited).
