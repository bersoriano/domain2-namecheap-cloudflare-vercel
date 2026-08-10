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
