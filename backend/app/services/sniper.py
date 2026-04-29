"""
Market Sniper — agent-driven entry timing for short-interval markets.

How it works:
  1. Every 30s, scan configured series for markets closing within
     SNIPE_OBSERVE_SECONDS (default 5 min). These enter a "watch" state.
  2. For each watched market, call agent.analyze_snipe() which returns:
       - entry_timing: ENTER_NOW | WAIT | SKIP
       - entry_delay_seconds: how long to wait before re-evaluating
  3. The agent sees live ticker data (price, momentum, volume) and decides
     when conditions are right — not a hardcoded clock.
  4. Once ENTER_NOW is returned, execute immediately.
  5. Markets are dropped from watch when they close or SKIP is returned.

Stop-loss:
  Every 60s, check open positions via ticker. Sell if loss >= STOP_LOSS_PCT.

Env:
  SNIPE_SERIES_SLUGS    — series to watch (default: crypto-sol-5min,crypto-btc-5min,...)
  SNIPE_OBSERVE_SECONDS — how far out to start watching (default: 300 = 5 min)
  SNIPE_MIN_SECONDS     — abort if less than this many seconds remain (default: 8)
  STOP_LOSS_PCT         — loss fraction to trigger sell (default: 0.35)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayse_client import get_bayse_client
from app.services.trade_executor import execute_signal
from app.utils.logger import logger
from app.websocket_manager import manager

# market_id -> asyncio.Task — one watcher task per active market
_watch_tasks: dict[str, asyncio.Task] = {}
# market_ids that have already been executed this session — prevent duplicate bets
_executed_markets: set[str] = set()


# Timing helpers

def _seconds_until_close(event: dict) -> Optional[float]:
    raw = event.get("closingDate") or event.get("resolutionDate")
    if not raw:
        return None
    try:
        raw_str = str(raw).replace("Z", "+00:00")
        close_dt = datetime.fromisoformat(raw_str)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        return (close_dt - datetime.now(tz=timezone.utc)).total_seconds()
    except Exception:
        return None


def _sell_order_amount(trade: Trade, current_sell_price: float) -> float:
    if trade.shares and trade.shares > 0:
        shares = float(trade.shares)
    else:
        price = float(trade.price or 0.0)
        if price > 0:
            shares = max(float(round(float(trade.total_cost or 0.0) / price)), 1.0)
        else:
            return 0.0

    if current_sell_price <= 0:
        return 0.0

    return round(shares * current_sell_price, 2)


# Per-market watcher coroutine

async def _watch_market(market_id: str, event: dict):
    """
    Continuously ask the agent whether to enter, wait, or skip.
    Runs until the market closes, the agent says SKIP, or we execute.
    """
    from app.services.ai_agent import get_agent
    min_secs: int = getattr(settings, "snipe_min_seconds", 8)
    client = get_bayse_client()
    agent = get_agent()
    title = event.get("title", market_id)

    try:
        while True:
            secs = _seconds_until_close(event)
            if secs is None or secs < min_secs:
                logger.info("Sniper: market '%s' closed or too late (%.0fs), dropping", title, secs or 0)
                break

            output = await agent.analyze_snipe(market_id, event, secs)

            if output is None:
                # LLM failed — wait 15s and retry
                await asyncio.sleep(15)
                continue

            timing = output.entry_timing
            logger.info(
                "Sniper: '%s' %.0fs left | signal=%s timing=%s delay=%ds conf=%d ev=%.2f",
                title, secs, output.signal, timing,
                output.entry_delay_seconds, output.confidence, output.expected_value,
            )

            if timing == "SKIP" or output.signal in ("HOLD", "AVOID"):
                logger.info("Sniper: agent skipped '%s'", title)
                break

            if timing == "ENTER_NOW" and output.signal in ("BUY_YES", "BUY_NO"):
                # Prevent duplicate execution for the same market
                if market_id in _executed_markets:
                    logger.info("Sniper: already executed '%s' this session, skipping", title)
                    break
                # Re-check seconds remaining — analysis takes time
                secs_now = _seconds_until_close(event)
                if secs_now is None or secs_now < min_secs:
                    logger.info("Sniper: market '%s' closed before we could execute (%.0fs left)", title, secs_now or 0)
                    break
                try:
                    async with AsyncSessionLocal() as session:
                        from app.services.storage import save_signal
                        from app.services.risk_guard import risk_guard, check_trade_limits
                        from app.services.config_service import get_config
                        from app.services.analysis import calculate_expected_value

                        cfg = await get_config(session)
                        portfolio = await client.get_portfolio() or {}
                        # Use real wallet balance, not portfolio market value
                        wallet_bal = await client.get_wallet_balance()
                        if wallet_bal > 0:
                            portfolio["_wallet_balance"] = wallet_bal

                        # Quick risk check before firing
                        rg = risk_guard(output.model_dump(), portfolio, cfg)
                        if not rg.passed:
                            logger.info("Sniper: risk guard blocked '%s': %s", title, rg.reasons)
                            break

                        tl = await check_trade_limits(session, cfg, portfolio=portfolio)
                        if not tl.passed:
                            logger.info("Sniper: trade limits blocked '%s': %s", title, tl.reasons)
                            break

                        saved = await save_signal(session, {
                            "market_id": market_id,
                            "market_name": output.market_name,
                            "signal": output.signal,
                            "confidence": output.confidence,
                            "estimated_probability": output.estimated_probability,
                            "current_market_price": output.current_market_price,
                            "expected_value": output.expected_value,
                            "reasoning": output.reasoning,
                            "sources": output.sources,
                            "suggested_stake": output.suggested_stake,
                            "risk_level": output.risk_level,
                            "event_id": event.get("id") or "",
                            "rank_score": None,
                        })
                        await execute_signal(session, client, saved, event_data=event)

                    logger.info(
                        "Sniper: EXECUTED %s on '%s' (%.0fs to close)",
                        output.signal, title, secs,
                    )
                    _executed_markets.add(market_id)
                    await manager.broadcast({
                        "type": "snipe_executed",
                        "data": {
                            "market_id": market_id,
                            "market_name": title,
                            "signal": output.signal,
                            "seconds_to_close": round(secs, 1),
                            "confidence": output.confidence,
                        },
                    })
                except Exception as exc:
                    logger.error("Sniper: execute failed for '%s': %s", title, exc, exc_info=True)
                break  # done regardless of success

            # WAIT — sleep for the agent-specified delay then re-evaluate
            delay = max(output.entry_delay_seconds or 15, 5)
            # Don't wait longer than time remaining minus buffer
            delay = min(delay, max(secs - min_secs - 2, 5))
            logger.info("Sniper: waiting %ds before re-evaluating '%s'", delay, title)
            await asyncio.sleep(delay)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Sniper watcher crashed for %s: %s", market_id, exc, exc_info=True)
    finally:
        _watch_tasks.pop(market_id, None)


# Snipe scan — runs every 30s

async def snipe_scan():
    """Detect markets entering the observation window and spawn watcher tasks."""
    observe_secs: int = getattr(settings, "snipe_observe_seconds", 300)
    min_secs: int = getattr(settings, "snipe_min_seconds", 8)
    slugs_raw: str = getattr(settings, "snipe_series_slugs", "crypto-sol-5min,crypto-btc-5min")
    series_slugs = [s.strip() for s in slugs_raw.split(",") if s.strip()]

    client = get_bayse_client()

    for slug in series_slugs:
        try:
            result = await client.list_events(status="open", series_slug=slug, size=20)
            events = (result or {}).get("events", [])
        except Exception as exc:
            logger.warning("Sniper: failed to list series '%s': %s", slug, exc)
            continue

        for event in events:
            secs = _seconds_until_close(event)
            if secs is None or secs < min_secs or secs > observe_secs:
                continue

            for market in event.get("markets") or []:
                market_id = market.get("id")
                if not market_id:
                    continue
                # Skip closed/resolved markets
                if market.get("status", "open") != "open":
                    continue
                if market_id in _watch_tasks and not _watch_tasks[market_id].done():
                    continue  # already watching
                if market_id in _executed_markets:
                    continue  # already traded this market this session

                logger.info(
                    "Sniper: starting watcher for '%s' (%.0fs to close, series=%s)",
                    event.get("title", market_id), secs, slug,
                )
                task = asyncio.create_task(_watch_market(market_id, event))
                _watch_tasks[market_id] = task


# Stop-loss monitor — runs every 15s

async def stop_loss_scan():
    """
    Sell positions that have lost more than STOP_LOSS_PCT.
    Uses portfolio outcomeBalances (live from Bayse) for accurate P&L —
    not the ticker which shows near-zero prices as markets approach close.
    """
    stop_loss_pct: float = getattr(settings, "stop_loss_pct", 0.35)
    client = get_bayse_client()

    try:
        # Fetch live portfolio — source of truth for current position values
        portfolio = await client.get_portfolio() or {}
        balances = portfolio.get("outcomeBalances") or []
        if not balances:
            return

        # Build market_id -> balance entry map
        balance_by_market: dict = {}
        for b in balances:
            if not b:
                continue
            market = b.get("market") or {}
            mid = market.get("id") or b.get("marketId")
            if mid:
                balance_by_market[mid] = b

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == "EXECUTED", Trade.resolution.is_(None))
            )
            trades = list(result.scalars().all())
            if not trades:
                return

            for trade in trades:
                try:
                    b = balance_by_market.get(trade.market_id)
                    if not b:
                        continue  # not in portfolio — may have resolved

                    cost = float(b.get("cost") or b.get("totalCost") or trade.total_cost or 0)
                    current_value = float(b.get("currentValue") or b.get("value") or 0)

                    if cost <= 0:
                        continue

                    loss_pct = (cost - current_value) / cost
                    if loss_pct < stop_loss_pct:
                        continue

                    sig = None
                    if trade.signal_id:
                        sig_r = await session.execute(select(Signal).where(Signal.id == trade.signal_id))
                        sig = sig_r.scalars().first()

                    outcome_label = "NO" if (sig and sig.signal_type in ("BUY_NO", "NO")) else "YES"
                    event_id = (sig.event_id if sig else None) or trade.market_id

                    logger.info(
                        "Stop-loss: market=%s cost=₦%.2f now=₦%.2f loss=%.1f%%",
                        trade.market_id, cost, current_value, loss_pct * 100,
                    )

                    min_amount = client.minimum_order_amount(client.default_currency)
                    if current_value < min_amount:
                        logger.warning(
                            "Stop-loss skipped for trade %s: current value ₦%.2f below Bayse minimum ₦%.2f",
                            trade.id, current_value, min_amount,
                        )
                        continue

                    await client.place_order(
                        event_id=event_id,
                        market_id=trade.market_id,
                        side="SELL",
                        outcome=outcome_label,
                        amount=current_value,
                        order_type="MARKET",
                        currency=client.default_currency,
                    )
                    trade.status = "SOLD"
                    trade.resolution = "STOP_LOSS"
                    session.add(trade)
                    await session.commit()

                    await manager.broadcast({
                        "type": "stop_loss_triggered",
                        "data": {"market_id": trade.market_id, "loss_pct": round(loss_pct * 100, 2)},
                    })

                except Exception as exc:
                    logger.warning("Stop-loss check failed for trade %s: %s", trade.id, exc)

    except Exception as exc:
        logger.error("Stop-loss scan failed: %s", exc, exc_info=True)
