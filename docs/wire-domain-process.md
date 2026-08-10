# wire-domain — Process Diagrams

Visual explanation of how `wire-domain` takes an **already-registered** domain and
wires it end-to-end: **Namecheap (registrar) → Cloudflare (authoritative DNS) →
Vercel (hosting)**. Every step is idempotent — re-running is always safe.

---

## 1. Architecture at a glance

How the pieces fit together. The CLI validates config, the orchestrator runs the
steps, and three thin provider wrappers isolate each external API.

```mermaid
flowchart TD
    User([User]) -->|wire / status| CLI["cli.py<br/>(Typer app)"]
    CLI --> Config["config.py<br/>load + validate .env<br/>masked table"]
    CLI --> Flow["flow.py<br/>Orchestrator"]

    Flow --> NC["providers/namecheap.py<br/>get/set nameservers"]
    Flow --> CF["providers/cloudflare.py<br/>zone + DNS records"]
    Flow --> VC["providers/vercel.py<br/>add project domain"]
    Flow --> State["state.py<br/>per-domain JSON<br/>(cache / audit)"]

    NC -->|namecheap-python| NCAPI[(Namecheap API)]
    CF -->|cloudflare SDK| CFAPI[(Cloudflare API)]
    VC -->|httpx REST| VCAPI[(Vercel API)]

    Flow --> Report["render_report()<br/>rich status table"]
    Report --> User

    classDef ext fill:#f5f5f5,stroke:#999,color:#333;
    class NCAPI,CFAPI,VCAPI ext;
```

---

## 2. The `wire` command — full flow

The four idempotent steps in order. Each step detects existing state first, so it
**skips**, **updates**, or **creates** as needed. Any `WireError` stops the flow and
exits `2`.

```mermaid
flowchart TD
    Start([wire domain --project P]) --> Load{"load + validate<br/>.env config"}
    Load -->|missing vars| Cfg1["print missing<br/>exit 1"]
    Load -->|ok| Proj{"resolve Vercel project<br/>--project or VERCEL_PROJECT"}
    Proj -->|none| Cfg2["error<br/>exit 1"]
    Proj -->|resolved| Confirm{"--yes or --dry-run?"}
    Confirm -->|no| Ask{"confirm prompt"}
    Ask -->|abort| Abort([aborted])
    Ask -->|proceed| S1
    Confirm -->|yes| S1

    subgraph Steps["Idempotent steps (stop on any error → exit 2)"]
        direction TB
        S1["① Cloudflare: get-or-create zone"] --> S1r{"zone exists?"}
        S1r -->|yes| S1skip["reuse zone<br/>status: skipped"]
        S1r -->|no| S1new["create zone<br/>status: created"]
        S1skip --> S2
        S1new --> S2

        S2["② Namecheap: nameservers"] --> S2c{"current NS ==<br/>Cloudflare NS?"}
        S2c -->|yes| S2skip["skip<br/>status: skipped"]
        S2c -->|no| S2set["set_custom(CF nameservers)<br/>stamp changed_at<br/>status: updated"]
        S2skip --> S3
        S2set --> S3

        S3["③ Cloudflare: ensure records<br/>A @ → 76.76.21.21<br/>CNAME www → cname.vercel-dns.com"] --> S3c{"record exists<br/>and correct?"}
        S3c -->|correct| S3skip["skip"]
        S3c -->|wrong| S3upd["update"]
        S3c -->|missing| S3new["create"]
        S3skip --> S4
        S3upd --> S4
        S3new --> S4

        S4["④ Vercel: add domain (+ www)"] --> S4c{"already on<br/>project?"}
        S4c -->|yes| S4skip["skip"]
        S4c -->|no| S4add["POST add domain<br/>409 elsewhere → error"]
        S4skip --> Done
        S4add --> Done
    end

    Done["render report + propagation note"] --> Exit0([exit 0 if ok, else 2])

    classDef err fill:#fde,stroke:#c66,color:#600;
    class Cfg1,Cfg2 err;
```

---

## 3. Idempotency decision (applies to every step)

The rule each provider follows. This is why the tool is safe to re-run at any time —
**live provider state is authoritative**, the state file is only a cache/audit trail.

```mermaid
flowchart LR
    A["read live state<br/>from provider"] --> B{"exists?"}
    B -->|no| C["create → created"]
    B -->|yes| D{"matches<br/>desired?"}
    D -->|yes| E["skip → skipped"]
    D -->|no| F["update → updated"]

    classDef ok fill:#efe,stroke:#6a6,color:#060;
    class C,E,F ok;
```

---

## 4. The `wire` command — sequence view

Same flow as a sequence, showing who talks to whom (happy path, first run).

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant CLI as cli.py
    participant O as Orchestrator
    participant CF as Cloudflare
    participant NC as Namecheap
    participant VC as Vercel
    participant ST as StateStore

    U->>CLI: wire example.com --project app
    CLI->>CLI: load + validate .env
    CLI->>O: wire(plan)

    O->>CF: get_or_create_zone(example.com)
    CF-->>O: ZoneInfo(id, nameservers, created)
    O->>ST: save(zone_id, nameservers)

    O->>NC: get_nameservers(example.com)
    NC-->>O: current NS
    alt NS differ from Cloudflare
        O->>NC: set_custom(CF nameservers)
        O->>ST: save(nameservers_changed_at)
    end

    O->>CF: ensure_record(A @ → 76.76.21.21)
    O->>CF: ensure_record(CNAME www → cname.vercel-dns.com)

    O->>VC: add_domain(example.com)
    O->>VC: add_domain(www.example.com)
    O->>VC: close()

    O-->>CLI: WireReport
    CLI-->>U: rich table + "NS may take up to 48h"
```

---

## 5. The `status` command — read-only

`status` never mutates anything. It inspects live state across all three providers
and reports. If no zone exists yet, it stops early.

```mermaid
flowchart TD
    S([status domain]) --> Z{"Cloudflare<br/>zone exists?"}
    Z -->|no| Zp["report: pending<br/>(stop)"]
    Z -->|yes| NS{"Namecheap NS ==<br/>Cloudflare NS?"}
    NS -->|yes| NSok["nameservers: skipped"]
    NS -->|no| NSpend["nameservers: pending"]
    NSok --> V
    NSpend --> V
    V{"domain(s) on<br/>Vercel project?"}
    V -->|attached| Vok["vercel: skipped"]
    V -->|missing| Vpend["vercel: pending"]
    Vok --> R([render report])
    Vpend --> R
```

---

## 6. Exit codes

```mermaid
flowchart LR
    R0["0 — success"]:::ok
    R1["1 — configuration error<br/>(missing env / no project)"]:::warn
    R2["2 — a step failed<br/>(provider error)"]:::err

    classDef ok fill:#efe,stroke:#6a6,color:#060;
    classDef warn fill:#ffe,stroke:#cc6,color:#660;
    classDef err fill:#fde,stroke:#c66,color:#600;
```

---

## Notes

- **Registration is out of scope** — the domain must already exist in the Namecheap
  account. Namecheap's only job here is the nameserver switch.
- **Nameserver propagation** can take minutes to 48 hours. The Cloudflare zone stays
  `pending` until it completes; `wire-domain` reports this but does not wait.
- **State file** (`~/.wire-domain/state/<domain>.json`) is a cache and audit trail
  only. Deleting it never changes correctness — live provider state always wins.
