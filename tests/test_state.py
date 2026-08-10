from pathlib import Path

from wire_domain.state import DomainState, StateStore, default_state_dir


def test_load_missing_returns_default(tmp_path: Path):
    store = StateStore(tmp_path)
    state = store.load("example.com")
    assert isinstance(state, DomainState)
    assert state.domain == "example.com"
    assert state.zone_id is None
    assert state.cloudflare_nameservers == []


def test_save_then_load_roundtrip(tmp_path: Path):
    store = StateStore(tmp_path)
    state = DomainState(domain="example.com", zone_id="zone123")
    state.cloudflare_nameservers = ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    state.last_completed_step = "cloudflare-zone"
    store.save(state)

    assert store.path_for("example.com") == tmp_path / "example.com.json"
    loaded = store.load("example.com")
    assert loaded.zone_id == "zone123"
    assert loaded.cloudflare_nameservers == ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    assert loaded.last_completed_step == "cloudflare-zone"
    assert loaded.updated_at is not None  # stamped on save


def test_save_creates_dir(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    store = StateStore(nested)
    store.save(DomainState(domain="x.com"))
    assert (nested / "x.com.json").exists()


def test_default_state_dir_shape():
    d = default_state_dir()
    assert d.name == "state"
    assert d.parent.name == ".wire-domain"
