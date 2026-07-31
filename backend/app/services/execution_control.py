from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_market import EventMarket
from app.models.feature_snapshot import FeatureSnapshot
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayse_client import BayseClient
from app.services.config_service import get_config
from app.services.risk_guard import check_trade_limits, risk_guard
from app.services.trade_executor import execute_signal
from app.utils.logger import logger


_trade_execution_lock = asyncio.Lock()


def execution_key(event_id: str | None, market_id: str) -> str:
    event_part = (event_id or "").strip() or "no-event"
    return f"{event_part}:{market_id}"


def _signal_payload(signal: Signal) -> dict[str, Any]:
    return {k: v for k, v in signal.__dict__.items() if not k.startswith("_")}


async def _require_feature_snapshot(
    session: AsyncSession,
    signal: Signal,
    *,
    event_id: str | None = None,
) -> bool:
    """
    Require a linked feature snapshot before execution.

    The analysis path is responsible for writing the snapshot and linking it to
    the signal. If that step did not happen, we fail closed and block trading.
    """
    recent_snapshot = await session.execute(
        select(FeatureSnapshot)
        .where(
            FeatureSnapshot.market_id == signal.market_id,
            FeatureSnapshot.resolved_signal_id == signal.id,
        )
        .order_by(FeatureSnapshot.created_at.desc())
        .limit(1)
    )
    snapshot = recent_snapshot.scalar_one_or_none()
    if snapshot is not None:
        return True

    if event_id:
        logger.info(
            "Execution blocked for %s (%s): no linked feature snapshot for signal %s",
            signal.market_id,
            event_id,
            signal.id,
        )
    else:
        logger.info(
            "Execution blocked for %s: no linked feature snapshot for signal %s",
            signal.market_id,
            signal.id,
        )
    return False


async def market_execution_exists(
    session: AsyncSession,
    *,
    event_id: str | None,
    market_id: str,
) -> bool:
    """
    Return True when this event/market already has a completed trade path.

    We check both the persistent event-market status and the trade history so a
    restart cannot lose the duplicate-suppression state.
    """
    if event_id:
        event_market = await session.get(EventMarket, {"event_id": event_id, "market_id": market_id})
        if event_market and event_market.status == "COMPLETED":
            return True

        trade_result = await session.execute(
            select(Trade.id)
            .join(Signal, Signal.id == Trade.signal_id)
            .where(
                Signal.event_id == event_id,
                Trade.market_id == market_id,
            )
            .limit(1)
        )
        if trade_result.first() is not None:
            return True
        return False

    trade_result = await session.execute(
        select(Trade.id)
        .where(Trade.market_id == market_id)
        .limit(1)
    )
    return trade_result.first() is not None


async def mark_market_completed(
    session: AsyncSession,
    *,
    event_id: str | None,
    market_id: str,
) -> None:
    """
    Persist a completed execution so future runs can skip the same market.
    """
    if not event_id:
        return

    event_market = await session.get(EventMarket, {"event_id": event_id, "market_id": market_id})
    if event_market is None:
        event_market = EventMarket(event_id=event_id, market_id=market_id)

    event_market.status = "COMPLETED"
    event_market.last_analyzed_at = datetime.utcnow()
    session.add(event_market)
    await session.commit()
    await session.refresh(event_market)


@asynccontextmanager
async def trade_execution_window():
    """
    Serialize the final risk-check -> order-placement section in-process.

    This prevents the agent cycle, sniper, and manual approval path from
    racing each other inside the same worker.
    """
    async with _trade_execution_lock:
        yield


async def execute_signal_with_controls(
    session: AsyncSession,
    client: BayseClient,
    signal: Signal,
    *,
    event_data: dict | None = None,
    amount_override: float | None = None,
) -> bool:
    """
    Gate order execution behind a serialized check so concurrent jobs cannot
    both pass the cap check and submit orders at the same time.
    """
    event_id = (signal.event_id or "").strip() or ""
    if event_data:
        event_id = (event_data.get("id") or event_data.get("eventId") or event_id or "").strip()
    event_id = event_id or None

    if await market_execution_exists(session, event_id=event_id, market_id=signal.market_id):
        logger.info(
            "Skipping execution for %s (%s) because a trade already exists",
            signal.market_id,
            event_id or "no-event",
        )
        return False

    async with trade_execution_window():
        if await market_execution_exists(session, event_id=event_id, market_id=signal.market_id):
            logger.info(
                "Skipping execution for %s (%s) because a trade already exists",
                signal.market_id,
                event_id or "no-event",
            )
            return False

        cfg = await get_config(session)
        portfolio = await client.get_portfolio() or {}
        wallet_balance = await client.get_wallet_balance()
        portfolio["_wallet_balance"] = float(wallet_balance or 0.0)

        signal_payload = _signal_payload(signal)
        rg = risk_guard(signal_payload, {**portfolio, "_wallet_balance": wallet_balance}, cfg)
        if not rg.passed:
            logger.info(
                "Execution blocked by risk guard for %s (%s): %s",
                signal.market_id,
                signal.market_name,
                rg.reasons,
            )
            return False

        tl = await check_trade_limits(session, cfg, portfolio=portfolio)
        if not tl.passed:
            logger.info(
                "Execution blocked by trade limits for %s (%s): %s",
                signal.market_id,
                signal.market_name,
                tl.reasons,
            )
            return False

        if not await _require_feature_snapshot(session, signal, event_id=event_id):
            return False

        await execute_signal(
            session,
            client,
            signal,
            amount_override=amount_override,
            event_data=event_data,
        )
        await mark_market_completed(session, event_id=event_id, market_id=signal.market_id)
        return True
