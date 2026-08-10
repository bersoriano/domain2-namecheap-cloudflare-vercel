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
