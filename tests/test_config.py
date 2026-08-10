import pytest

from wire_domain.config import Settings, load_settings, mask
from wire_domain.errors import ConfigError

REQUIRED = {
    "NAMECHEAP_API_USER": "user1",
    "NAMECHEAP_API_KEY": "key123456",
    "NAMECHEAP_USERNAME": "user1",
    "NAMECHEAP_CLIENT_IP": "1.2.3.4",
    "CLOUDFLARE_API_TOKEN": "cftoken123",
    "VERCEL_TOKEN": "vtoken123",
}


def test_mask_short_and_empty():
    assert mask("") == "—"
    assert mask("abcdef") == "abcd…"


def test_load_settings_happy_path():
    s = load_settings(env=REQUIRED)
    assert isinstance(s, Settings)
    assert s.namecheap_api_user == "user1"
    assert s.namecheap_sandbox is False  # default
    assert s.cloudflare_account_id is None
    assert s.vercel_project is None


def test_sandbox_parsed_truthy():
    s = load_settings(env={**REQUIRED, "NAMECHEAP_SANDBOX": "true"})
    assert s.namecheap_sandbox is True


def test_optional_fields_passthrough():
    s = load_settings(
        env={**REQUIRED, "VERCEL_TEAM_ID": "team_x", "VERCEL_PROJECT": "proj_y", "CLOUDFLARE_ACCOUNT_ID": "acct_z"}
    )
    assert s.vercel_team_id == "team_x"
    assert s.vercel_project == "proj_y"
    assert s.cloudflare_account_id == "acct_z"


def test_missing_required_lists_all():
    with pytest.raises(ConfigError) as exc:
        load_settings(env={"NAMECHEAP_API_USER": "u"})
    msg = str(exc.value)
    assert "NAMECHEAP_API_KEY" in msg
    assert "CLOUDFLARE_API_TOKEN" in msg
    assert "VERCEL_TOKEN" in msg
    assert "NAMECHEAP_API_USER" not in msg  # provided, not missing
