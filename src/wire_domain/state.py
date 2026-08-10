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
