import pytest

from wire_domain.errors import (
    CloudflareProviderError,
    ConfigError,
    NamecheapError,
    VercelError,
    WireError,
)
from wire_domain.models import RecordSpec, StepResult, WireReport


def test_error_hierarchy_and_cause():
    cause = ValueError("boom")
    err = NamecheapError("failed to set ns", cause=cause)
    assert isinstance(err, WireError)
    assert err.cause is cause
    assert "failed to set ns" in str(err)
    for cls in (ConfigError, CloudflareProviderError, VercelError):
        assert issubclass(cls, WireError)


def test_report_ok_and_add():
    report = WireReport(domain="example.com")
    assert report.ok is True
    report.add(StepResult(name="cloudflare-zone", status="created"))
    report.add(StepResult(name="namecheap-ns", status="skipped", detail="already set"))
    assert report.ok is True
    report.add(StepResult(name="vercel", status="failed", detail="401"))
    assert report.ok is False
    assert len(report.steps) == 3


def test_record_spec_defaults():
    spec = RecordSpec(type="A", name="@", content="76.76.21.21", proxied=False)
    assert spec.ttl == 1
