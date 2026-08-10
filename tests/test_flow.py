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
