# wire-domain

Wire an already-registered domain: **Namecheap → Cloudflare → Vercel**.

`wire-domain` automates the end-to-end configuration of a registered domain across three providers: it updates the nameservers in Namecheap, creates a Cloudflare zone and wires DNS records, then connects the domain to your Vercel project.

## Prerequisites

- The domain is already registered in your Namecheap account.
- Namecheap API access enabled and your public IP whitelisted (`NAMECHEAP_CLIENT_IP`).
- A Cloudflare API token with **Zone:Edit** and **DNS:Edit** scopes.
- A Vercel token and target project.

## Install

```bash
uv sync
```

## Configure

Copy `.env.example` to `.env` and fill in the required API credentials and configuration:

```bash
cp .env.example .env
# Then edit .env with your API keys and settings
```

### Environment Variables

- **Namecheap:**
  - `NAMECHEAP_API_USER` — your Namecheap API user
  - `NAMECHEAP_API_KEY` — your Namecheap API key
  - `NAMECHEAP_USERNAME` — your Namecheap account username
  - `NAMECHEAP_CLIENT_IP` — your whitelisted public IP
  - `NAMECHEAP_SANDBOX` (optional, default `false`) — use sandbox API

- **Cloudflare:**
  - `CLOUDFLARE_API_TOKEN` — token with Zone:Edit + DNS:Edit (required)
  - `CLOUDFLARE_ACCOUNT_ID` (optional) — only needed if token sees multiple accounts

- **Vercel:**
  - `VERCEL_TOKEN` — your Vercel personal token (required)
  - `VERCEL_PROJECT` (optional) — default project; can be overridden with `--project`
  - `VERCEL_TEAM_ID` (optional) — only if token is team-scoped

## Commands

### Wire a domain

```bash
# Full wire (asks for confirmation first)
uv run wire-domain wire example.com --project my-project

# Preview without changing anything
uv run wire-domain wire example.com --project my-project --dry-run

# Non-interactive (skip confirmation prompt)
uv run wire-domain wire example.com --project my-project --yes

# Apex only, no www
uv run wire-domain wire example.com --project my-project --no-www

# With verbose error output (full tracebacks)
uv run wire-domain wire example.com --project my-project --verbose
```

**Flags for `wire`:**
- `--project PROJECT` — Vercel project name (or `VERCEL_PROJECT` env var)
- `--www` / `--no-www` — create www CNAME and add www domain to Vercel (default: `--www`)
- `--dry-run` — print intended changes without applying them
- `--yes` — skip confirmation prompt and proceed immediately
- `--state-dir PATH` — directory for idempotency state (default: `.wire-domain-state`)
- `--verbose` — print full tracebacks on error

### Check domain status

```bash
# Read-only status across all three providers
uv run wire-domain status example.com --project my-project

# Check status without www
uv run wire-domain status example.com --project my-project --no-www

# With verbose error output
uv run wire-domain status example.com --project my-project --verbose
```

**Flags for `status`:**
- `--project PROJECT` — Vercel project name (or `VERCEL_PROJECT` env var)
- `--www` / `--no-www` — also check www subdomain (default: `--www`)
- `--state-dir PATH` — directory for idempotency state (default: `.wire-domain-state`)
- `--verbose` — print full tracebacks on error

## DNS Records Created

The following DNS records are created or ensured on Cloudflare:

| Type | Name | Value | Proxied | When |
|------|------|-------|---------|------|
| `A` | `@` | `76.76.21.21` | off | always |
| `CNAME` | `www` | `cname.vercel-dns.com` | off | only with `--www` (default) |

## Behavior

### Idempotency

Every step detects existing state and skips or updates as needed, so re-running `wire` is safe. For example:
- If the Namecheap nameservers are already set correctly, they are skipped.
- If the Cloudflare zone already exists, it is reused.
- If DNS records or Vercel domains are already configured, they are updated in-place.

The tool maintains a state file (`.wire-domain-state/` by default) to track progress; you can change the location with `--state-dir`.

### Nameserver Propagation

Nameserver changes can take minutes to 48 hours to propagate globally. The Cloudflare zone stays **pending** until nameserver propagation completes. The tool reports the zone status but does not wait for propagation — you can check status later with `uv run wire-domain status <domain>`.

## Exit Codes

- `0` — success
- `1` — configuration error (missing or invalid env vars, invalid project)
- `2` — a step failed (API error, network issue, or validation failure)

## Examples

Wire a domain with verbose output to see detailed logs:

```bash
uv run wire-domain wire example.com --project my-project --verbose
```

Preview all changes without applying them (useful for testing):

```bash
uv run wire-domain wire example.com --project my-project --dry-run
```

Operate on apex domain only (no www):

```bash
uv run wire-domain wire example.com --project my-project --no-www --yes
```

Inspect the current DNS and domain configuration across all providers:

```bash
uv run wire-domain status example.com --project my-project
```
