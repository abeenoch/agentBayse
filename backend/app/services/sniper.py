"""
Market Sniper - agent-driven entry timing for short-interval markets.

How it works:
  1. Every 30s, scan configured series for markets closing within
     SNIPE_OBSERVE_SECONDS (default 5 min). These enter a "watch" state.
  2. For each watched market, call agent.analyze_snipe() which returns:
       - entry_timing: ENTER_NOW | WAIT | SKIP
       - entry_delay_seconds: how long to wait before re-evaluating
  3. The agent sees live ticker data (price, momentum, volume) and decides
     when conditions are right - not a hardcoded clock.
  4. Once ENTER_NOW is returned, execute immediately.
  5. Markets are dropped from watch when they close or SKIP is returned.

Stop-loss:
  Every 60s, check open positions via ticker. Sell if loss >= STOP_LOSS_PCT.

Env:
  SNIPE_SERIES_SLUGS    - series to watch (default: crypto-btc-1h,crypto-eth-1h,crypto-sol-1h)
  SNIPE_OBSERVE_SECONDS - how far out to start watching (default: 300 = 5 min)
  SNIPE_MIN_SECONDS     - abort if less than this many seconds remain (default: 8)
  STOP_LOSS_PCT         - loss fraction to trigger sell (default: 0.35)
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
from app.services.bayes_state_keys import build_bayes_state_key_candidates, resolve_bayes_state_key
from app.services.execution_control import (
    execute_signal_with_controls,
    execution_key,
    market_execution_exists,
)
from app.services.outcome_sync import sync_signal_outcome
from app.utils.logger import logger
from app.websocket_manager import manager

# event_id:market_id -> asyncio.Task - one watcher task per active market
_watch_tasks: dict[str, asyncio.Task] = {}


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


async def _watch_market(market_id: str, event: dict, market: dict):
    """
    Continuously ask the agent whether to enter, wait, or skip.
    Runs until the market closes, the agent says SKIP, or we execute.
    """
    from app.services.ai_agent import get_agent
    min_secs: int = getattr(settings, "snipe_min_seconds", 8)
    client = get_bayse_client()
    agent = get_agent()
    title = event.get("title", market_id)
    event_id = (event.get("id") or "").strip()
    watch_key = execution_key(event_id, market_id)

    try:
        async with AsyncSessionLocal() as session:
            while True:
                secs = _seconds_until_close(event)
                if secs is None or secs < min_secs:
                    logger.info("Sniper: market '%s' closed or too late (%.0fs), dropping", title, secs or 0)
                    break

                output = await agent.analyze_snipe(market_id, event, secs, session=session)
                if output is None:
                    await asyncio.sleep(15)
                    continue

                timing = output.entry_timing
                logger.info(
                    "Sniper: '%s' %.0fs left | signal=%s timing=%s delay=%ds conf=%d ev=%.2f",
                    title,
                    secs,
                    output.signal,
                    timing,
                    output.entry_delay_seconds,
                    output.confidence,
                    output.expected_value,
                )

                if timing == "SKIP" or output.signal in ("HOLD", "AVOID"):
                    logger.info("Sniper: agent skipped '%s'", title)
                    break

                if timing == "ENTER_NOW" and output.signal in ("BUY_YES", "BUY_NO"):
                    secs_now = _seconds_until_close(event)
                    if secs_now is None or secs_now < min_secs:
                        logger.info("Sniper: market '%s' closed before we could execute (%.0fs left)", title, secs_now or 0)
                        break

                    try:
                        from app.services.storage import save_signal
                        from app.services.config_service import get_config

                        if await market_execution_exists(session, event_id=event_id or None, market_id=market_id):
                            logger.info("Sniper: already executed '%s' for event %s, skipping", title, event_id or "unknown")
                            break

                        cfg = await get_config(session)
                        series_slug = event.get("seriesSlug") or event.get("series_slug")
                        category = event.get("category") or market.get("category")
                        bayes_state_key = await resolve_bayes_state_key(
                            session,
                            build_bayes_state_key_candidates(
                                market_id=market_id,
                                event_id=event.get("id") or "",
                                series_slug=series_slug,
                                category=category,
                                default_key=getattr(cfg, "bayes_state_key", "default") or "default",
                            ),
                            default_key=getattr(cfg, "bayes_state_key", "default") or "default",
                        )

                        saved = await save_signal(
                            session,
                            {
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
                                "bayes_state_key": bayes_state_key,
                            },
                        )

                        executed = await execute_signal_with_controls(
                            session,
                            client,
                            saved,
                            event_data=event,
                        )
                    except Exception as exc:
                        logger.error("Sniper: execute failed for '%s': %s", title, exc, exc_info=True)
                        break

                    if executed:
                        logger.info(
                            "Sniper: EXECUTED %s on '%s' (%.0fs to close)",
                            output.signal,
                            title,
                            secs,
                        )
                        await manager.broadcast(
                            {
                                "type": "snipe_executed",
                                "data": {
                                    "market_id": market_id,
                                    "market_name": title,
                                    "signal": output.signal,
                                    "seconds_to_close": round(secs, 1),
                                    "confidence": output.confidence,
                                },
                            }
                        )
                    else:
                        logger.info("Sniper: execution skipped for '%s'", title)
                    break

                delay = max(output.entry_delay_seconds or 15, 5)
                delay = min(delay, max(secs - min_secs - 2, 5))
                logger.info("Sniper: waiting %ds before re-evaluating '%s'", delay, title)
                await asyncio.sleep(delay)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Sniper watcher crashed for %s: %s", market_id, exc, exc_info=True)
    finally:
        _watch_tasks.pop(watch_key, None)


async def snipe_scan():
    """Detect markets entering the observation window and spawn watcher tasks."""
    observe_secs: int = getattr(settings, "snipe_observe_seconds", 300)
    min_secs: int = getattr(settings, "snipe_min_seconds", 8)
    slugs_raw: str = getattr(settings, "snipe_series_slugs", "crypto-btc-1h,crypto-eth-1h,crypto-sol-1h")
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
                if market.get("status", "open") != "open":
                    continue
                watch_key = execution_key(event.get("id") or "", market_id)
                if watch_key in _watch_tasks and not _watch_tasks[watch_key].done():
                    continue
                async with AsyncSessionLocal() as session:
                    if await market_execution_exists(session, event_id=event.get("id") or None, market_id=market_id):
                        continue

                logger.info(
                    "Sniper: starting watcher for '%s' (%.0fs to close, series=%s)",
                    event.get("title", market_id),
                    secs,
                    slug,
                )
                task = asyncio.create_task(_watch_market(market_id, event, market))
                _watch_tasks[watch_key] = task



def _real_currency(api_value: float | int | None, balance_obj: dict | None) -> float:
    """
    Convert a Bayse portfolio API value (fractional scale) to actual currency amount.

    Bayse returns cost/currentValue in a fractional share-price scale (0-1).
    For NGN: 1 unit = ₦100 (each winning share pays ₦100)
    For USD: 1 unit =  (each winning share pays )
    """
    if api_value is None:
        return 0.0
    currency = str((balance_obj or {}).get("currency", "")).strip().upper()
    if currency == "NGN":
        return float(api_value) * 100.0
    return float(api_value)  # USD and others are 1:1


async def stop_loss_scan():
    """
    Sell positions that have lost more than STOP_LOSS_PCT.
    Iterates over ALL portfolio positions (not just DB-tracked trades).
    """
    stop_loss_pct: float = getattr(settings, "stop_loss_pct", 0.0)
    if stop_loss_pct <= 0:
        return

    client = get_bayse_client()

    try:
        portfolio = await client.get_portfolio() or {}
        balances = portfolio.get("outcomeBalances") or []
        if not balances:
            return

        # Index DB trades by market_id for extra context
        async with AsyncSessionLocal() as session:
            trade_result = await session.execute(
                select(Trade).where(Trade.status == "EXECUTED", Trade.resolution.is_(None))
            )
            db_trades = {t.market_id: t for t in trade_result.scalars().all()}

            for b in balances:
                if not b:
                    continue
                market = b.get("market") or {}
                mid = market.get("id") or b.get("marketId")
                if not mid:
                    continue

                try:
                    # Bayse API uses fractional scale (0-1). Convert to real currency.
                    raw_cost = float(b.get("cost") or 0)
                    raw_current = float(b.get("currentValue") or 0)
                    cost = _real_currency(raw_cost, b)
                    current_value = _real_currency(raw_current, b)

                    if cost <= 0:
                        continue

                    loss_pct = (cost - current_value) / cost
                    if loss_pct < stop_loss_pct:
                        continue

                    # Use DB trade for context if available
                    trade = db_trades.get(mid)
                    outcome_label = "YES"
                    event_id = market.get("event", {}).get("id", "") or mid
                    if trade and trade.signal_id:
                        sig_r = await session.execute(select(Signal).where(Signal.id == trade.signal_id))
                        sig = sig_r.scalars().first()
                        if sig:
                            outcome_label = "NO" if sig.signal_type in ("BUY_NO", "NO") else "YES"
                            event_id = sig.event_id or event_id

                    logger.info(
                        "Stop-loss: market=%s cost=\₦%.2f now=\₦%.2f loss=%.1f%%",
                        mid, cost, current_value, loss_pct * 100,
                    )

                    min_amount = client.minimum_order_amount(client.default_currency)
                    if current_value < min_amount:
                        logger.warning(
                            "Stop-loss skipped for %s: value \u20a6%.2f below minimum \u20a6%.2f",
                            mid, current_value, min_amount,
                        )
                        continue

                    await client.place_order(
                        event_id=event_id,
                        market_id=mid,
                        side="SELL",
                        outcome=outcome_label,
                        amount=current_value,
                        order_type="MARKET",
                        currency=client.default_currency,
                    )

                    if trade:
                        trade.status = "STALE"
                        session.add(trade)
                        await session.commit()

                    logger.info("Stop-loss: sell order sent for market %s", mid)

                except Exception as exc:
                    logger.warning("Stop-loss failed for market %s: %s", mid, exc, exc_info=True)

    except Exception as exc:
        logger.warning("Stop-loss scan failed: %s", exc, exc_info=True)

async def take_profit_scan():
    """
    Sell positions that have gained more than TAKE_PROFIT_PCT.
    Iterates over ALL portfolio positions (not just DB-tracked trades).

    Supports two modes:
      - Full exit (take_profit_partial_exit=False): sells entire position
      - Partial exit (take_profit_partial_exit=True): sells enough to recover cost
        basis, leaving the rest as a "free bet" riding to resolution
    """
    take_profit_pct: float = getattr(settings, "take_profit_pct", 0.0)
    if take_profit_pct <= 0:
        return

    partial_exit: bool = getattr(settings, "take_profit_partial_exit", True)
    client = get_bayse_client()

    try:
        portfolio = await client.get_portfolio() or {}
        balances = portfolio.get("outcomeBalances") or []
        if not balances:
            return

        async with AsyncSessionLocal() as session:
            trade_result = await session.execute(
                select(Trade).where(Trade.status == "EXECUTED", Trade.resolution.is_(None))
            )
            db_trades = {t.market_id: t for t in trade_result.scalars().all()}

            for b in balances:
                if not b:
                    continue
                market = b.get("market") or {}
                mid = market.get("id") or b.get("marketId")
                if not mid:
                    continue

                try:
                    raw_cost = float(b.get("cost") or 0)
                    raw_current = float(b.get("currentValue") or 0)
                    cost = _real_currency(raw_cost, b)
                    current_value = _real_currency(raw_current, b)

                    if cost <= 0 or current_value <= 0:
                        continue

                    profit_pct = (current_value - cost) / cost
                    if profit_pct < take_profit_pct:
                        continue

                    trade = db_trades.get(mid)
                    outcome_label = "YES"
                    event_id = market.get("event", {}).get("id", "") or mid
                    if trade and trade.signal_id:
                        sig_r = await session.execute(select(Signal).where(Signal.id == trade.signal_id))
                        sig = sig_r.scalars().first()
                        if sig:
                            outcome_label = "NO" if sig.signal_type in ("BUY_NO", "NO") else "YES"
                            event_id = sig.event_id or event_id

                    if partial_exit:
                        sell_amount = round(cost, 2)
                        if sell_amount < client.minimum_order_amount(client.default_currency):
                            logger.info(
                                "Take-profit (partial) skipped for %s: cost basis \u20a6%.2f "
                                "below minimum \u20a6%.2f — selling full instead",
                                mid, sell_amount, client.minimum_order_amount(client.default_currency),
                            )
                            sell_amount = current_value
                            partial_label = "full"
                        else:
                            partial_label = "partial (cost basis recovered)"
                    else:
                        sell_amount = current_value
                        partial_label = "full"

                    logger.info(
                        "Take-profit: market=%s cost=\₦%.2f now=\₦%.2f profit=%.1f%% mode=%s",
                        mid, cost, current_value, profit_pct * 100, partial_label,
                    )

                    min_amount = client.minimum_order_amount(client.default_currency)
                    if sell_amount < min_amount:
                        logger.warning(
                            "Take-profit skipped for %s: sell amount \u20a6%.2f below "
                            "Bayse minimum \u20a6%.2f",
                            mid, sell_amount, min_amount,
                        )
                        continue

                    await client.place_order(
                        event_id=event_id,
                        market_id=mid,
                        side="SELL",
                        outcome=outcome_label,
                        amount=sell_amount,
                        order_type="MARKET",
                        currency=client.default_currency,
                    )

                    if partial_exit and sell_amount < current_value:
                        logger.info(
                            "Take-profit (partial): sold \u20a6%.2f, \u20a6%.2f remains as free bet for market %s",
                            sell_amount, current_value - sell_amount, mid,
                        )
                    elif trade:
                        trade.status = "STALE"
                        session.add(trade)
                        await session.commit()
                        logger.info("Take-profit (full): sell order sent for market %s", mid)
                    else:
                        logger.info("Take-profit: sell order sent for portfolio position %s", mid)

                except Exception as exc:
                    logger.warning("Take-profit failed for market %s: %s", mid, exc, exc_info=True)

    except Exception as exc:
        logger.warning("Take-profit scan failed: %s", exc, exc_info=True)

