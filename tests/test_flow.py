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
    def __init__(self, zone_created=True, zone_exists=True):
        self._zone_created = zone_created
        self._zone_exists = zone_exists
        self.records = []
        self.get_or_create_calls = 0

    def get_zone(self, domain):
        # Read-only lookup. Returns an existing zone or None.
        return FakeZoneInfo(created=False) if self._zone_exists else None

    def get_or_create_zone(self, domain):
        self.get_or_create_calls += 1
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


class FakeCloudflareWithZone(FakeCloudflare):
    def __init__(self, zone=None):
        super().__init__()
        self._zone = zone
    def get_zone(self, domain):
        return self._zone


class FakeVercelWithList(FakeVercel):
    def __init__(self, attached=None):
        super().__init__()
        self._attached = attached or []
    def list_domains(self):
        return list(self._attached)


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


def test_dry_run_missing_zone_does_not_create(tmp_path):
    # A dry run must never create a Cloudflare zone. If none exists yet, the
    # whole plan is a pending preview and get_or_create_zone is not called.
    nc = FakeNamecheap(current_ns=[])
    cf = FakeCloudflare(zone_exists=False)  # get_zone -> None
    vc = FakeVercel()
    orch = build(tmp_path, nc, cf, vc)
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=True))

    assert cf.get_or_create_calls == 0          # never created
    assert nc.set_calls == []                    # no writes anywhere
    assert cf.records == []
    assert vc.added == []
    assert not (tmp_path / "example.com.json").exists()  # no state persisted
    assert report.ok is True
    assert all(s.status == "pending" for s in report.steps)
    zone_step = next(s for s in report.steps if s.name == "cloudflare-zone")
    assert zone_step.status == "pending"


def test_dry_run_existing_zone_reuses_without_create(tmp_path):
    nc = FakeNamecheap(current_ns=["dns1.registrar-servers.com"])
    cf = FakeCloudflare(zone_exists=True)  # get_zone -> existing zone
    vc = FakeVercel()
    orch = build(tmp_path, nc, cf, vc)
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=True))

    assert cf.get_or_create_calls == 0                    # reused, not created
    assert not (tmp_path / "example.com.json").exists()   # no state persisted
    zone_step = next(s for s in report.steps if s.name == "cloudflare-zone")
    assert zone_step.status == "skipped"


def test_failed_step_detail_includes_cause(tmp_path):
    from wire_domain.errors import CloudflareProviderError

    class BoomCF(FakeCloudflare):
        def get_or_create_zone(self, domain):
            raise CloudflareProviderError(
                "Failed to create zone for example.com",
                cause=RuntimeError("403 insufficient permissions"),
            )

    orch = build(tmp_path, FakeNamecheap([]), BoomCF(), FakeVercel())
    report = orch.wire(WirePlan("example.com", "proj", include_www=True, dry_run=False))
    failed = report.steps[0]
    assert failed.status == "failed"
    assert "Failed to create zone" in failed.detail
    assert "403 insufficient permissions" in failed.detail  # underlying cause surfaced


def test_render_report_does_not_emoji_mangle_step_names():
    from wire_domain.console import console
    from wire_domain.flow import render_report
    from wire_domain.models import StepResult, WireReport

    report = WireReport(domain="example.com")
    report.add(StepResult("cloudflare-record:A:@", "pending", "would ensure 76.76.21.21"))
    with console.capture() as cap:
        render_report(report)
    out = cap.get()
    assert "🅰" not in out           # ":A:" must not become an emoji
    assert "cloudflare-record" in out


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


def test_status_reports_pending_when_zone_absent(tmp_path):
    orch = Orchestrator(
        settings=make_settings(), state_store=StateStore(tmp_path),
        namecheap=FakeNamecheap(current_ns=[]),
        cloudflare=FakeCloudflareWithZone(zone=None),
        vercel_factory=lambda project: FakeVercelWithList([]),
    )
    report = orch.status("example.com", "proj", include_www=True)
    zone_step = next(s for s in report.steps if s.name == "cloudflare-zone")
    assert zone_step.status == "pending"
    # zone absent -> status returns early, no vercel/ns steps
    assert len(report.steps) == 1


def test_status_reports_attached_and_ns_matching(tmp_path):
    zone = FakeZoneInfo(created=False)  # name_servers = ns1/ns2.cloudflare.com, status "pending"
    orch = Orchestrator(
        settings=make_settings(), state_store=StateStore(tmp_path),
        namecheap=FakeNamecheap(current_ns=["ns1.cloudflare.com", "ns2.cloudflare.com"]),
        cloudflare=FakeCloudflareWithZone(zone=zone),
        vercel_factory=lambda project: FakeVercelWithList(["example.com", "www.example.com"]),
    )
    report = orch.status("example.com", "proj", include_www=True)
    names = {s.name: s.status for s in report.steps}
    assert names["cloudflare-zone"] == "skipped"          # zone exists
    assert names["namecheap-nameservers"] == "skipped"    # NS already match
    assert names["vercel-domain:example.com"] == "skipped"      # attached
    assert names["vercel-domain:www.example.com"] == "skipped"  # attached
