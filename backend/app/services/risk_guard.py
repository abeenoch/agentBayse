from dataclasses import dataclass
from typing import List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.trade import Trade
from app.services.config_service import DEFAULT_CONFIG


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: List[str]


def risk_guard(signal: dict, portfolio: dict, cfg=None) -> RiskCheckResult:
    if settings.mock_mode:
        return RiskCheckResult(passed=True, reasons=[])

    reasons: List[str] = []

    min_conf = (getattr(cfg, "min_confidence", None) if cfg is not None else None)
    min_conf = min_conf if min_conf is not None else getattr(settings, "agent_min_confidence", 60) or 60

    reserve_pct = (getattr(cfg, "balance_reserve_pct", None) if cfg is not None else None)
    reserve_pct = reserve_pct if reserve_pct is not None else settings.agent_balance_reserve_pct

    stake = signal.get("suggested_stake", 0)
    if stake > settings.agent_max_position_size:
        reasons.append("stake exceeds max position size")

    # Try wallet balance first (real cash), then fall back to portfolio fields
    balance = (
        portfolio.get("_wallet_balance")
        or portfolio.get("portfolioCurrentValue")
        or portfolio.get("availableBalance")
        or portfolio.get("walletBalance")
        or portfolio.get("balance")
    )

    if balance and float(balance) > 0 and not settings.agent_ignore_balance_check:
        balance = float(balance)
        deployed = float(portfolio.get("portfolioCost") or 0.0)
        deployable = balance * (1.0 - reserve_pct)
        remaining_deployable = max(deployable - deployed, 0.0)

        if stake > remaining_deployable:
            reasons.append(
                f"balance reserve breach — deployable=₦{remaining_deployable:.0f} "
                f"(keeping {reserve_pct*100:.0f}% of ₦{balance:.0f} as reserve)"
            )
    # If balance is 0 or unavailable and ignore_balance_check is True, allow through

    if signal.get("expected_value") is not None and signal["expected_value"] < 0:
        reasons.append("non-positive EV")

    # EV must exceed the flat ₦5 Bayse fee as a percentage of stake
    # e.g. ₦100 stake → need EV > 5, ₦500 stake → need EV > 1, ₦1000 → need EV > 0.5
    stake = signal.get("suggested_stake") or 100.0
    bayse_fee = 5.0
    min_ev = (bayse_fee / max(stake, 1.0)) * 100  # as % of stake, scaled to EV units
    ev = signal.get("expected_value")
    if ev is not None and 0 <= ev < min_ev:
        reasons.append(f"EV too low ({ev:.2f} < {min_ev:.2f} needed to cover ₦5 fee on ₦{stake:.0f} stake)")

    if signal.get("confidence", 0) < min_conf:
        reasons.append("low confidence")

    return RiskCheckResult(passed=len(reasons) == 0, reasons=reasons)


@dataclass
class TradeLimitResult:
    passed: bool
    reasons: List[str]


async def check_trade_limits(session: AsyncSession, cfg, portfolio: dict | None = None) -> TradeLimitResult:
    """
    Check open position count using Bayse portfolio as source of truth.
    Falls back to DB count if portfolio is unavailable.
    """
    reasons: List[str] = []
    max_open = getattr(cfg, "max_open_positions", DEFAULT_CONFIG.get("max_open_positions", 3))

    # Primary: use live outcomeBalances from Bayse portfolio (real source of truth)
    if portfolio is not None:
        outcome_balances = portfolio.get("outcomeBalances") or []
        open_count = len([b for b in outcome_balances if b])
        source = "bayse"
    else:
        # Fallback: DB count
        open_trades_q = await session.execute(
            select(func.count()).select_from(Trade).where(
                Trade.status == "EXECUTED",
                Trade.resolution.is_(None),
            )
        )
        open_count = open_trades_q.scalar_one()
        source = "db"

    if open_count >= max_open:
        reasons.append(
            f"simultaneous trade cap reached ({open_count}/{max_open} open, source={source})"
        )

    return TradeLimitResult(passed=len(reasons) == 0, reasons=reasons)
