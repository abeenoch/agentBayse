from datetime import datetime, timezone
from typing import List, Dict, Any
import re
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.services.ai_agent import get_agent
from app.services.bayse_client import get_bayse_client
from app.database import AsyncSessionLocal
from app.services.config_service import get_config
from app.services.payout_reconciliation import (
    apply_activity_to_trade,
    index_payout_activities,
    match_payout_activity_for_trade,
)
from app.utils.logger import logger
from app.models.signal import Signal
from app.models.trade import Trade
from app.models.event_market import EventMarket
from sqlalchemy import select

scheduler = AsyncIOScheduler()
pending_markets: List[Dict] = []
ORDER_MONITOR_JOB_ID = "order_monitor"
SNIPER_JOB_ID = "sniper"
STOP_LOSS_JOB_ID = "stop_loss"
TAKE_PROFIT_JOB_ID = "take_profit"
SIGNAL_RECONCILE_JOB_ID = "signal_reconcile"

TRAINING_JOB_ID = "bayes_training"
WATCHLIST_KEYWORDS = {
    # BTC with 15-minute timeframe
    "btc": ["btc", "bitcoin"],
    "btc_15m": ["15m", "15 m", "15min", "15 min", "15-minute", "15 minute"],
    # USD/NGN and GBP/NGN (hourly targets, but allow match even if hour text missing)
    "usd_ngn": ["usd/ngn", "usd to ngn", "dollar to naira", "dollar-naira", "usd ngn", "usdngn"],
    "gbp_ngn": ["gbp/ngn", "gbp to ngn", "pound to naira", "pound-naira", "gbp ngn", "gbpngn"],
    "one_hour": ["1h", "1 h", "1hr", "1 hr", "1hour", "1 hour", "hourly", "hour"],
}

CURRENCY_TERMS = {
    "usd": ["usd", "us dollar", "u.s. dollar", "dollar"],
    "gbp": ["gbp", "pound", "british pound", "pound sterling", "sterling"],
    "ngn": ["ngn", "naira", "nigerian naira"],
    "eur": ["eur", "euro"],
}
_CURRENCY_CODES_PATTERN = "|".join(sorted(CURRENCY_TERMS.keys()))
_CURRENCY_PAIR_SEPARATORS = r"(?:/|\bto\b|\bvs\b|\bagainst\b|\bper\b|-|\s+)"


def _normalize_currency_terms(text: str) -> str:
    """Replace common currency names with their ISO codes for easier pair matching."""
    normalized = text.lower()
    for code, terms in CURRENCY_TERMS.items():
        for term in terms:
            normalized = re.sub(rf"\b{re.escape(term)}\b", code, normalized)
    return normalized


def _extract_currency_pair(text: str) -> tuple[str, str] | None:
    """Detect any currency/currency pair like USD/GBP, GBP to USD, etc."""
    normalized = _normalize_currency_terms(text)
    match = re.search(
        rf"\b({_CURRENCY_CODES_PATTERN})\s*{_CURRENCY_PAIR_SEPARATORS}\s*({_CURRENCY_CODES_PATTERN})\b",
        normalized,
    )
    if not match:
        # Fallback for concatenated forms like "usdngn" with no separator.
        match = re.search(
            rf"\b({_CURRENCY_CODES_PATTERN})({_CURRENCY_CODES_PATTERN})\b",
            normalized,
        )
    if not match:
        return None
    c1, c2 = match.groups()
    if c1 == c2:
        return None
    # order-insensitive so USD/GBP and GBP/USD map to the same reason
    return tuple(sorted((c1, c2)))


def _watchlist_reason(event: Dict, market: Dict) -> str | None:
    text = " ".join(
        [
            str(event.get("title", "")),
            str(event.get("description", "")),
            str(market.get("title", "")),
        ]
    )
    text_lower = text.lower()

    # BTC 15m only
    if any(k in text_lower for k in WATCHLIST_KEYWORDS["btc"]) and any(
        k in text_lower for k in WATCHLIST_KEYWORDS["btc_15m"]
    ):
        return "btc_15m"

    if ("nigeria" in text_lower or "lagos" in text_lower) and (
        "temp" in text_lower or "temperature" in text_lower or "weather" in text_lower
    ):
        return "ng_weather"

    pair = _extract_currency_pair(text)
    if pair:
        return f"fx_{pair[0]}_{pair[1]}"

    return None


def _matches_watchlist(event: Dict, market: Dict) -> bool:
    return _watchlist_reason(event, market) is not None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_activity_timestamp(activity: dict) -> datetime | None:
    raw = activity.get("createdAt") or activity.get("updatedAt")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return _as_utc(parsed)
    except Exception:
        return None


def _extract_activity_list(payload: dict | None) -> list[dict]:
    """
    Normalize the different response shapes Bayse may return.

    The production API has changed shape before, and our client also uses a
    `data_stale` sentinel when requests fail. Treat that sentinel as an error
    source instead of silently reconciling against an empty list.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("data_stale"):
        raise RuntimeError("Bayse activity feed unavailable")

    if isinstance(payload.get("activities"), list):
        return list(payload["activities"])

    data = payload.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("activities"), list):
            return list(data["activities"])
        if isinstance(data.get("items"), list):
            return list(data["items"])

    if isinstance(payload.get("items"), list):
        return list(payload["items"])

    return []


def _format_age(reference: datetime | None, now: datetime | None = None) -> dict[str, Any]:
    current = _as_utc(now or datetime.utcnow())
    ref = _as_utc(reference)
    if ref is None:
        return {"seconds": None, "human": None}
    delta = max((current - ref).total_seconds(), 0.0)
    if delta < 60:
        human = f"{int(delta)}s"
    elif delta < 3600:
        human = f"{delta / 60:.1f}m"
    else:
        human = f"{delta / 3600:.1f}h"
    return {"seconds": round(delta, 2), "human": human}


def _candidate_trade_filter(include_stale_expired: bool = False):
    from sqlalchemy import or_, and_

    live_statuses = {"EXECUTED", "SOLD"}
    live_clause = and_(Trade.status.in_(live_statuses), Trade.resolution.is_(None))
    if not include_stale_expired:
        return live_clause
    stale_clause = and_(Trade.status == "STALE", Trade.resolution == "EXPIRED")
    expired_clause = and_(Trade.status.in_(live_statuses), Trade.resolution == "EXPIRED")
    return or_(live_clause, stale_clause, expired_clause)


async def _fetch_payout_activities_since(
    client,
    *,
    cutoff: datetime | None,
    page_size: int = 100,
    max_pages: int = 10,
) -> list[dict]:
    """
    Fetch payout activities newest-first until we reach activities older than cutoff.

    This keeps reconciliation bounded while still catching up after long downtimes.
    """
    cutoff_utc = _as_utc(cutoff)
    collected: list[dict] = []

    for page in range(1, max_pages + 1):
        payload = await client.get_activities(type="payout", page=page, size=page_size)
        activities = _extract_activity_list(payload)
        if not activities:
            break

        for activity in activities:
            ts = _parse_activity_timestamp(activity)
            if ts is not None and cutoff_utc is not None and ts < cutoff_utc:
                continue
            collected.append(activity)

        # If the oldest item on this page is already older than our cutoff, the
        # next page should only be older as well, so stop paging.
        if cutoff_utc is not None:
            page_times = [ts for ts in (_parse_activity_timestamp(a) for a in activities) if ts is not None]
            if page_times and min(page_times) < cutoff_utc:
                break

    return collected


async def collect_live_trade_diagnostics(
    session,
    client,
    *,
    include_stale_expired: bool = False,
    page_size: int = 100,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """
    Build a read-only view of unresolved live trades plus Bayse match status.
    """
    result = await session.execute(
        select(Trade).where(_candidate_trade_filter(include_stale_expired=include_stale_expired))
    )
    trades = list(result.scalars().all())
    if not trades:
        return []

    oldest_live_trade = min(
        (_as_utc(t.created_at) for t in trades if t.created_at is not None),
        default=None,
    )
    bayse_fetch_error = None
    try:
        activities = await _fetch_payout_activities_since(
            client,
            cutoff=oldest_live_trade,
            page_size=page_size,
            max_pages=max_pages,
        )
    except Exception as exc:
        bayse_fetch_error = str(exc)
        activities = []
    payout_by_order, payout_by_event_market = index_payout_activities(activities)
    now = datetime.utcnow()

    diagnostics: list[dict[str, Any]] = []
    for trade in trades:
        signal = None
        if trade.signal_id:
            signal = await session.get(Signal, trade.signal_id)

        activity = None
        match_type = None
        if trade.bayse_order_id:
            activity = payout_by_order.get(str(trade.bayse_order_id))
            if activity:
                match_type = "order_id"

        if activity is None and signal and signal.event_id:
            activity = payout_by_event_market.get((str(signal.event_id), str(trade.market_id)))
            if activity:
                match_type = "event_market"

        activity_ts = _parse_activity_timestamp(activity) if activity else None
        diagnostics.append({
            "trade_id": str(trade.id),
            "signal_id": str(trade.signal_id) if trade.signal_id else None,
            "market_id": trade.market_id,
            "market_name": trade.market_name,
            "status": trade.status,
            "resolution": trade.resolution,
            "pnl": trade.pnl,
            "created_at": trade.created_at.isoformat() if trade.created_at else None,
            "executed_at": trade.executed_at.isoformat() if trade.executed_at else None,
            "resolved_at": trade.resolved_at.isoformat() if trade.resolved_at else None,
            "age": _format_age(trade.executed_at or trade.created_at, now=now),
            "bayse_order_id": trade.bayse_order_id,
            "bayse_match": {
                "matched": activity is not None,
                "match_type": match_type,
                "activity_type": activity.get("type") if activity else None,
                "resolved_outcome": activity.get("resolvedOutcome") if activity else None,
                "payout": activity.get("payout") if activity else None,
                "activity_created_at": activity_ts.isoformat() if activity_ts else None,
            },
            "bayse_fetch_error": bayse_fetch_error,
        })

    return diagnostics


async def populate_queue():
    """
    Populate the pending_markets queue using exact seriesSlug filters.

    Series slugs are fetched from the configured AGENT_SERIES_SLUGS setting,
    which defaults to all known automated series. This is far more reliable
    than keyword-matching event titles.
    """
    pending_markets.clear()
    client = get_bayse_client()
    events: List[Dict] = []

    async with AsyncSessionLocal() as session:
        cfg = await get_config(session)

        # Build the list of series slugs to scan
        slugs_raw: str = getattr(settings, "agent_series_slugs", "")
        if slugs_raw:
            series_slugs = [s.strip() for s in slugs_raw.split(",") if s.strip()]
        else:
            # Default: 1-hour crypto focus for higher edge
            series_slugs = ["crypto-btc-1h", "crypto-eth-1h", "crypto-sol-1h"]

        # Optionally filter by categories from DB config
        cfg_categories = {c.lower() for c in (cfg.categories or [])}

        for slug in series_slugs:
            try:
                result = await client.list_events(
                    status="open",
                    series_slug=slug,
                    size=settings.agent_event_page_size,
                )
                slug_events = (result or {}).get("events", [])
                if cfg_categories:
                    slug_events = [
                        e for e in slug_events
                        if (e.get("category") or "").lower() in cfg_categories
                    ]
                events.extend(slug_events)
            except Exception as exc:
                logger.warning("populate_queue: failed to fetch series '%s': %s", slug, exc)

        stats: Dict[str, int] = {}
        for event in events:
            event_id = event.get("id")
            series = event.get("seriesSlug", "unknown")
            for market in event.get("markets", []) or []:
                market_id = market.get("id")
                if not market_id or not event_id:
                    continue
                # Don't filter by COMPLETED here — market IDs repeat across series events.
                # The reanalysis cooldown in analyze_market handles deduplication.
                em = await session.get(EventMarket, {"event_id": event_id, "market_id": market_id})
                if not em:
                    session.add(EventMarket(event_id=event_id, market_id=market_id, status="PENDING"))
                pending_markets.append({"event": event, "market": market})
                stats[series] = stats.get(series, 0) + 1

        await session.commit()

    sample = [m["market"].get("title") or m["event"].get("title") for m in pending_markets][:5]
    stats_text = ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "none"
    logger.info(
        "Queue: %d markets from %d events. Series: %s. Sample: %s",
        len(pending_markets), len(events), stats_text, sample,
    )
    if not pending_markets:
        logger.info("No open markets found across configured series.")


async def run_agent_cycle():
    try:
        await populate_queue()
        if not pending_markets:
            logger.info("No markets queued; skipping cycle")
            return
        logger.info("Processing %d queued markets", len(pending_markets))
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select, func as sa_func
            from app.models.trade import Trade as TradeModel
            from app.services.config_service import get_config

            cfg = await get_config(session)
            max_open = getattr(cfg, "max_open_positions", settings.agent_max_open_positions)
            logger.info("Cycle start: checking live positions before each market (max=%d)", max_open)

            agent = get_agent()
            _client = get_bayse_client()
            while pending_markets:
                # Re-fetch live open count before each market — sniper may have traded mid-cycle
                _portfolio = await _client.get_portfolio() or {}
                open_count = len([b for b in (_portfolio.get("outcomeBalances") or []) if b])

                if open_count >= max_open:
                    logger.info("Open position cap (%d/%d) reached — stopping cycle", open_count, max_open)
                    pending_markets.clear()
                    break

                item = pending_markets.pop(0)
                event = item["event"]
                market = item["market"]
                try:
                    signal = await agent.analyze_market(market["id"], event=event, session=session)
                    if signal:
                        logger.info(
                            "Generated signal %s %s ev=%.2f",
                            signal.market_id,
                            signal.signal,
                            signal.expected_value,
                        )
                        # Increment in-cycle counter so next iteration sees the updated count
                        open_count += 1
                except Exception as market_err:
                    logger.error("Error analyzing market %s: %s", market.get("id"), market_err, exc_info=True)
    except Exception as exc:
        logger.error("Agent cycle failed: %s", exc, exc_info=True)


async def reconcile_open_trades(session, client) -> tuple[int, int]:
    """
    Reconcile unresolved trades against Bayse order and payout history.

    This is used both by the periodic order monitor and once at startup so a
    restarted server can catch up on outcomes that resolved while it was down.
    """
    result = await session.execute(
        select(Trade).where(_candidate_trade_filter(include_stale_expired=True))
    )
    trades = list(result.scalars().all())
    if not trades:
        return 0, 0

    oldest_live_trade = min(
        (_as_utc(t.created_at) for t in trades if t.created_at is not None),
        default=None,
    )
    payout_by_order: Dict[str, dict] = {}
    payout_by_event_market: Dict[tuple[str, str], dict] = {}
    try:
        acts = await _fetch_payout_activities_since(client, cutoff=oldest_live_trade)
        payout_by_order, payout_by_event_market = index_payout_activities(acts)
    except Exception as exc:
        logger.warning("reconcile_open_trades: failed to fetch payout activities: %s", exc)

    resolved_count = 0
    for t in trades:
        if not t.bayse_order_id:
            pass
        if t.bayse_order_id in {"CLOB", "AMM"}:
            logger.warning("Marking legacy order id %s for trade %s as STALE", t.bayse_order_id, t.id)
            t.status = "STALE"
            t.resolution = "EXPIRED"
            session.add(t)
            continue
        try:
            uuid.UUID(str(t.bayse_order_id))
        except Exception:
            if t.bayse_order_id:
                logger.warning("Marking non-UUID order id %s for trade %s as STALE", t.bayse_order_id, t.id)
                t.status = "STALE"
                t.resolution = "EXPIRED"
                session.add(t)
                continue
        try:
            if t.bayse_order_id:
                data = await client.get_order(t.bayse_order_id)
                if isinstance(data, dict) and data.get("data_stale"):
                    raise RuntimeError("Bayse order feed unavailable")
                status = data.get("status", "")

                if status in ("filled", "FILLED"):
                    if t.status == "STALE":
                        t.status = "EXECUTED"
                elif status in ("cancelled", "CANCELLED", "expired", "EXPIRED", "rejected", "REJECTED"):
                    if t.status == "STALE" and t.resolution == "EXPIRED":
                        # Keep the stale marker if Bayse still reports a terminal expiry.
                        session.add(t)
                    else:
                        t.status = status.upper()

            act, _sig = await match_payout_activity_for_trade(
                session,
                t,
                payout_by_order,
                payout_by_event_market,
            )
            if act:
                signal = await apply_activity_to_trade(session, t, act)
                if signal is not None:
                    resolved_count += 1
                session.add(t)
        except Exception as order_err:
            logger.warning("Failed to refresh order %s: %s", t.bayse_order_id, order_err)

    await session.commit()
    return len(trades), resolved_count


async def normalize_terminal_trades(session) -> int:
    """
    Normalize terminal-but-skipped trades into a reconciliable state.

    We only touch rows that are already terminal on the local side but were left
    in EXECUTED/EXPIRED by a partial sync. This keeps the repair narrow and safe.
    """
    from sqlalchemy import update

    result = await session.execute(
        update(Trade)
        .where(Trade.status == "EXECUTED", Trade.resolution == "EXPIRED")
        .values(status="STALE")
    )
    return int(result.rowcount or 0)


async def monitor_orders():
    """
    Poll for order resolution and P&L.
    Uses two sources per the Bayse docs:
      - GET /pm/orders/{id}  → order status (filled, cancelled, etc.)
      - GET /pm/activities?type=payout → PAYOUT_WIN / PAYOUT_LOSS with actual payout amounts
    """
    client = get_bayse_client()
    try:
        async with AsyncSessionLocal() as session:
            await normalize_terminal_trades(session)
            await reconcile_open_trades(session, client)
    except Exception as exc:
        logger.error("Order monitor failed: %s", exc, exc_info=True)


async def ensure_order_monitor_job():
    """Start order monitor only when at least one trade exists."""
    if scheduler.get_job(ORDER_MONITOR_JOB_ID):
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Trade.id).limit(1))
            has_trade = result.first() is not None
        if not has_trade:
            logger.info("Order monitor not scheduled (no trades yet).")
            return
        scheduler.add_job(
            monitor_orders,
            "interval",
            seconds=300,
            id=ORDER_MONITOR_JOB_ID,
            next_run_time=datetime.now(),
            max_instances=1,
            coalesce=True,
        )
        logger.info("Order monitor scheduled (trades detected).")
    except Exception as exc:
        logger.error("Failed to schedule order monitor: %s", exc, exc_info=True)


async def reconcile_signal_outcomes():
    """Periodically check unresolved non-executed signals against market outcomes."""
    client = get_bayse_client()
    try:
        async with AsyncSessionLocal() as session:
            from app.services.outcome_sync import reconcile_unexecuted_signals
            result = await reconcile_unexecuted_signals(session, client, max_signals=50)
            if result["resolved"] > 0:
                logger.info(
                    "Signal reconciliation: scanned=%d resolved=%d skipped=%d events_checked=%d",
                    result["scanned"],
                    result["resolved"],
                    result["skipped"],
                    result["events_checked"],
                )
    except Exception as exc:
        logger.error("Signal reconciliation failed: %s", exc, exc_info=True)




async def periodic_bayes_training():
    """Periodically retrain Bayes models for all active state keys."""
    from app.services.bayes_training import train_bayes_model
    state_keys = ["series:crypto-btc-1h", "series:crypto-eth-1h", "series:crypto-sol-1h"]
    try:
        async with AsyncSessionLocal() as session:
            for key in state_keys:
                try:
                    result = await train_bayes_model(session, state_key=key)
                    if result.get("sample_size", 0) > 0:
                        logger.info(
                            "Bayes training complete: key=%s samples=%d train=%d test=%d pos_rate=%.3f",
                            key,
                            result["sample_size"],
                            result["train_size"],
                            result["test_size"],
                            result["positive_rate"],
                        )
                except Exception as exc:
                    logger.warning("Bayes training failed for key %s: %s", key, exc, exc_info=True)
    except Exception as exc:
        logger.error("Bayes training cycle failed: %s", exc, exc_info=True)

def start_scheduler():
    if scheduler.running:
        return
    scheduler.add_job(
        run_agent_cycle,
        "interval",
        seconds=settings.agent_scan_interval_seconds,
        id="agent_cycle",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        ensure_order_monitor_job,
        "date",
        run_date=datetime.now(),
        id="order_monitor_init",
        max_instances=1,
    )
    # Sniper — runs every 30 seconds
    from app.services.sniper import snipe_scan, stop_loss_scan, take_profit_scan
    scheduler.add_job(
        snipe_scan,
        "interval",
        seconds=30,
        id=SNIPER_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    # Stop-loss monitor — runs every 15 seconds for responsive monitoring
    scheduler.add_job(
        stop_loss_scan,
        "interval",
        seconds=15,
        id=STOP_LOSS_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    # Take-profit monitor — runs every 20 seconds
    if settings.take_profit_pct > 0:
        scheduler.add_job(
            take_profit_scan,
            "interval",
            seconds=20,
            id=TAKE_PROFIT_JOB_ID,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Take-profit scanner enabled (threshold=%.0f%% partial=%s)",
                     settings.take_profit_pct * 100, settings.take_profit_partial_exit)
    # Signal outcome reconciliation — checks non-executed signals for outcomes
    if settings.signal_reconcile_interval_seconds > 0:
        scheduler.add_job(
            reconcile_signal_outcomes,
            "interval",
            seconds=settings.signal_reconcile_interval_seconds,
            id=SIGNAL_RECONCILE_JOB_ID,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Signal reconciliation enabled (interval=%ds weight=%.2f)",
                     settings.signal_reconcile_interval_seconds, settings.signal_outcome_weight)
    # Periodic Bayes model retraining — runs every 6 hours
    scheduler.add_job(
        periodic_bayes_training,
        "interval",
        hours=6,
        id=TRAINING_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(),  # Run immediately on startup
    )
    logger.info("Bayes training job scheduled (interval=6h, first run NOW)")
    logger.info(
        "Starting scheduler with agent cycle every %ss (first run NOW)",
        settings.agent_scan_interval_seconds,
    )
    scheduler.start()
