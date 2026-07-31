from types import SimpleNamespace

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
    thresh = getattr(settings, "agent_min_confidence", 60)
    sig = {"suggested_stake": 100, "expected_value": 10, "confidence": thresh - 5, "created_at": "2026-01-01T00:00:00"}
    portfolio = {"portfolioCurrentValue": 1000}
    result = risk_guard(sig, portfolio)
    assert not result.passed


def test_risk_guard_respects_explicit_zero_wallet_balance(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    cfg = SimpleNamespace(min_confidence=0, balance_reserve_pct=0.30)
    sig = {"suggested_stake": 800, "expected_value": 10, "confidence": 80, "created_at": "2026-01-01T00:00:00"}
    portfolio = {
        "_wallet_balance": 0.0,
        "portfolioCurrentValue": 10_000,
        "portfolioCost": 0.0,
    }
    result = risk_guard(sig, portfolio, cfg=cfg)
    assert result.passed
