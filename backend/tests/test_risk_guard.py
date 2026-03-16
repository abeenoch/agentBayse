from datetime import datetime, timedelta

from app.services.risk_guard import risk_guard


def test_risk_guard_blocks_big_stake(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "mock_mode", False)
    signal = {
        "suggested_stake": 1_000_000,
        "created_at": datetime.utcnow().isoformat(),
    }
    portfolio = {"portfolioCurrentValue": 1000}
    result = risk_guard(signal, portfolio)
    assert not result.passed
    assert "stake exceeds" in result.reasons[0]
