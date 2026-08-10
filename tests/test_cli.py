from pathlib import Path

import pytest
from typer.testing import CliRunner

import wire_domain.cli as cli_mod
from wire_domain.cli import app
from wire_domain.errors import ConfigError
from wire_domain.models import StepResult, WireReport

runner = CliRunner()

ENV = {
    "NAMECHEAP_API_USER": "u", "NAMECHEAP_API_KEY": "k", "NAMECHEAP_USERNAME": "u",
    "NAMECHEAP_CLIENT_IP": "1.2.3.4", "CLOUDFLARE_API_TOKEN": "c", "VERCEL_TOKEN": "v",
}


class FakeOrch:
    def __init__(self, ok=True):
        self._ok = ok

    def wire(self, plan):
        r = WireReport(domain=plan.domain)
        r.add(StepResult("cloudflare-zone", "created" if self._ok else "failed"))
        return r

    def status(self, domain, project, include_www):
        r = WireReport(domain=domain)
        r.add(StepResult("cloudflare-zone", "skipped"))
        return r


def patch_env(monkeypatch, env):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: cli_mod.load_settings.__wrapped__(env=env)
                        if False else _settings_from(env))


def _settings_from(env):
    from wire_domain.config import load_settings
    return load_settings(env=env)


def test_wire_success_exit_0(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch(ok=True))
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_wire_step_failure_exit_2(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch(ok=False))
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 2


def test_wire_config_error_exit_1(monkeypatch, tmp_path):
    def boom(**kw):
        raise ConfigError("Missing required configuration: VERCEL_TOKEN")
    monkeypatch.setattr(cli_mod, "load_settings", boom)
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Missing required configuration" in result.output


def test_wire_missing_project_exit_1(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from(ENV))  # no VERCEL_PROJECT
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch())
    result = runner.invoke(app, ["wire", "example.com", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "project" in result.output.lower()


def test_status_exit_0(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "load_settings", lambda **kw: _settings_from({**ENV, "VERCEL_PROJECT": "proj"}))
    monkeypatch.setattr(cli_mod, "build_orchestrator", lambda settings, state_dir: FakeOrch())
    result = runner.invoke(app, ["status", "example.com", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
