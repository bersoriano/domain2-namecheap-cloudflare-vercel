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
