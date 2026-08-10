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
