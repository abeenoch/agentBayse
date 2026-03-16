from app.services.risk_guard import risk_guard
from app.config import settings


def test_risk_guard_blocks_negative_ev(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    sig = {"suggested_stake": 100, "expected_value": -1, "confidence": 80, "created_at": "2026-01-01T00:00:00"}
    portfolio = {"portfolioCurrentValue": 1000}
    result = risk_guard(sig, portfolio)
    assert not result.passed
    assert any("EV" in r or "non" in r for r in result.reasons)


def test_risk_guard_blocks_low_confidence(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    sig = {"suggested_stake": 100, "expected_value": 10, "confidence": 50, "created_at": "2026-01-01T00:00:00"}
    portfolio = {"portfolioCurrentValue": 1000}
    result = risk_guard(sig, portfolio)
    assert not result.passed
