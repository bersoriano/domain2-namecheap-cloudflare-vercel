import httpx
import pytest

from wire_domain.config import Settings
from wire_domain.errors import VercelError
from wire_domain.providers.vercel import VercelProvider


def make_settings(team_id=None) -> Settings:
    return Settings(
        namecheap_api_user="u", namecheap_api_key="k", namecheap_username="u",
        namecheap_client_ip="1.2.3.4", namecheap_sandbox=True,
        cloudflare_api_token="c", cloudflare_account_id=None,
        vercel_token="vtoken", vercel_team_id=team_id, vercel_project=None,
    )


def make_provider(handler, project="proj", team_id=None) -> VercelProvider:
    return VercelProvider(make_settings(team_id=team_id), project=project,
                          transport=httpx.MockTransport(handler))


def test_list_domains():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v9/projects/proj/domains"
        return httpx.Response(200, json={"domains": [{"name": "example.com"}, {"name": "www.example.com"}]})

    p = make_provider(handler)
    assert p.list_domains() == ["example.com", "www.example.com"]


def test_add_domain_creates():
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"domains": []})
        calls["post"] += 1
        assert request.method == "POST"
        assert request.url.path == "/v10/projects/proj/domains"
        return httpx.Response(200, json={"name": "example.com"})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "created"
    assert calls["post"] == 1


def test_add_domain_skips_when_present():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # never POSTs
        return httpx.Response(200, json={"domains": [{"name": "example.com"}]})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "skipped"


def test_add_domain_409_but_now_present_is_skipped():
    state = {"added": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            names = [{"name": "example.com"}] if state["added"] else []
            return httpx.Response(200, json={"domains": names})
        state["added"] = True  # someone else added it concurrently
        return httpx.Response(409, json={"error": {"code": "domain_already_in_use"}})

    p = make_provider(handler)
    assert p.add_domain("example.com") == "skipped"


def test_add_domain_409_elsewhere_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"domains": []})
        return httpx.Response(409, json={"error": {"code": "domain_already_in_use"}})

    p = make_provider(handler)
    with pytest.raises(VercelError):
        p.add_domain("example.com")


def test_team_id_in_query():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("teamId") == "team_x"
        return httpx.Response(200, json={"domains": []})

    p = make_provider(handler, team_id="team_x")
    assert p.list_domains() == []


def test_auth_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "forbidden"}})

    p = make_provider(handler)
    with pytest.raises(VercelError):
        p.list_domains()


def test_team_id_in_add_domain_post():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.method] = request.url.params.get("teamId")
        if request.method == "GET":
            return httpx.Response(200, json={"domains": []})
        return httpx.Response(200, json={"name": "example.com"})

    p = make_provider(handler, team_id="team_x")
    assert p.add_domain("example.com") == "created"
    assert seen["GET"] == "team_x"
    assert seen["POST"] == "team_x"


def test_no_team_id_in_query_when_unset():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("teamId") is None
        return httpx.Response(200, json={"domains": []})

    p = make_provider(handler)  # team_id defaults to None
    assert p.list_domains() == []
