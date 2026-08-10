import pytest

from wire_domain.config import Settings
from wire_domain.errors import NamecheapError
from wire_domain.providers.namecheap import NamecheapProvider, nameservers_equal


def make_settings() -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=None,
        vercel_token="v", vercel_team_id=None, vercel_project=None,
    )


class FakeDns:
    def __init__(self, ns, set_result=None, raise_on_set=False):
        self._ns = ns
        self._set_result = set_result or {"IsSuccess": True}
        self._raise_on_set = raise_on_set
        self.set_calls = []

    def get_list(self, domain):
        return {"Domain": domain, "IsUsingOurDNS": False, "Nameservers": self._ns}

    def set_custom(self, domain, nameservers):
        if self._raise_on_set:
            raise RuntimeError("api down")
        self.set_calls.append((domain, nameservers))
        return self._set_result


class FakeClient:
    def __init__(self, dns):
        self.domains = type("D", (), {"dns": dns})()


def test_nameservers_equal_normalizes():
    assert nameservers_equal(["NS1.CF.com", "ns2.cf.com."], ["ns2.cf.com", "ns1.cf.com"])
    assert not nameservers_equal(["ns1.cf.com"], ["ns1.cf.com", "ns2.cf.com"])


def test_get_nameservers():
    dns = FakeDns(ns=["dns1.registrar-servers.com", "dns2.registrar-servers.com"])
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    assert p.get_nameservers("example.com") == [
        "dns1.registrar-servers.com",
        "dns2.registrar-servers.com",
    ]


def test_set_nameservers_success():
    dns = FakeDns(ns=[], set_result={"IsSuccess": True})
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])
    assert dns.set_calls == [("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])]


def test_set_nameservers_unsuccessful_raises():
    dns = FakeDns(ns=[], set_result={"IsSuccess": False, "Warnings": "nope"})
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    with pytest.raises(NamecheapError):
        p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])


def test_set_nameservers_exception_wrapped():
    dns = FakeDns(ns=[], raise_on_set=True)
    p = NamecheapProvider(make_settings(), client=FakeClient(dns))
    with pytest.raises(NamecheapError):
        p.set_nameservers("example.com", ["ns1.cloudflare.com", "ns2.cloudflare.com"])
