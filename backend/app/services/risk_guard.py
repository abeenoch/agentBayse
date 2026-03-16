from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.signal import Signal
from app.services.config_service import DEFAULT_CONFIG


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: List[str]


def risk_guard(signal: dict, portfolio: dict) -> RiskCheckResult:
    if settings.mock_mode:
        return RiskCheckResult(passed=True, reasons=[])

    reasons: List[str] = []

    stake = signal.get("suggested_stake", 0)
    if stake > settings.agent_max_position_size:
        reasons.append("stake exceeds max position size")

    trades_today = 0  # placeholder; will be computed from DB/activity later
    if trades_today >= settings.agent_max_daily_trades:
        reasons.append("daily trade limit reached")

    available_balance = portfolio.get("portfolioCurrentValue")
    if not settings.agent_ignore_balance_check and available_balance is not None and stake > available_balance:
        reasons.append("insufficient balance")

    if signal.get("expected_value") is not None and signal["expected_value"] <= 0:
        reasons.append("non-positive EV")

    if signal.get("confidence", 0) < 60:
        reasons.append("low confidence")

    created_at = signal.get("created_at") or datetime.utcnow()
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except Exception:
            created_at = datetime.utcnow()
    if datetime.utcnow() - created_at > timedelta(minutes=10):
        reasons.append("signal too old")

    return RiskCheckResult(passed=len(reasons) == 0, reasons=reasons)


@dataclass
class TradeLimitResult:
    passed: bool
    reasons: List[str]


async def check_trade_limits(session: AsyncSession, cfg) -> TradeLimitResult:
    reasons: List[str] = []
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)

    mth = getattr(cfg, "max_trades_per_hour", DEFAULT_CONFIG["max_trades_per_hour"])
    mtd = getattr(cfg, "max_trades_per_day", DEFAULT_CONFIG["max_trades_per_day"])

    qh = await session.execute(select(func.count()).select_from(Signal).where(Signal.created_at >= hour_ago))
    qd = await session.execute(select(func.count()).select_from(Signal).where(Signal.created_at >= day_ago))
    if qh.scalar_one() >= mth:
        reasons.append("hourly trade cap reached")
    if qd.scalar_one() >= mtd:
        reasons.append("daily trade cap reached")

    return TradeLimitResult(passed=len(reasons) == 0, reasons=reasons)
