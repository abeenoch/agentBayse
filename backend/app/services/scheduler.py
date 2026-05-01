from datetime import datetime
from typing import List, Dict
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
from app.models.trade import Trade
from app.models.event_market import EventMarket
from sqlalchemy import select

scheduler = AsyncIOScheduler()
pending_markets: List[Dict] = []
ORDER_MONITOR_JOB_ID = "order_monitor"
SNIPER_JOB_ID = "sniper"
STOP_LOSS_JOB_ID = "stop_loss"

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
            # Default: all automated series we know about
            series_slugs = [
                "crypto-btc-5min", "crypto-sol-5min", "crypto-eth-5min", "crypto-bnb-5min",
                "crypto-btc-15min", "crypto-sol-15min", "crypto-eth-15min",
                "crypto-btc-1h", "crypto-sol-1h", "crypto-eth-1h",
                "fx-usdngn-1h", "fx-gbpusd-1h", "fx-eurusd-1h",
                "fx-gbpjpy-1h", "fx-eurjpy-1h", "fx-usdjpy-1h",
                "commodity-xauusd-1h", "commodity-xagusd-1h",
            ]

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
            result = await session.execute(
                select(Trade).where(Trade.status == "EXECUTED", Trade.resolution.is_(None))
            )
            trades = list(result.scalars().all())
            if not trades:
                return

            # Fetch recent payout activities to match against our trades
            payout_by_order: Dict[str, dict] = {}
            payout_by_event_market: Dict[tuple[str, str], dict] = {}
            try:
                acts = await client.get_activities(type="payout", size=50)
                payout_by_order, payout_by_event_market = index_payout_activities((acts or {}).get("activities", []))
            except Exception as exc:
                logger.warning("monitor_orders: failed to fetch activities: %s", exc)

            for t in trades:
                if not t.bayse_order_id:
                    # Fallback matching can still resolve legacy rows via eventId/marketId.
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
                        status = data.get("status", "")

                        if status in ("filled", "FILLED"):
                            t.status = "EXECUTED"
                        elif status in ("cancelled", "CANCELLED", "expired", "EXPIRED", "rejected", "REJECTED"):
                            t.status = status.upper()

                    act, _sig = await match_payout_activity_for_trade(
                        session,
                        t,
                        payout_by_order,
                        payout_by_event_market,
                    )
                    if act:
                        await apply_activity_to_trade(session, t, act)
                        session.add(t)
                except Exception as order_err:
                    logger.warning("Failed to refresh order %s: %s", t.bayse_order_id, order_err)
            await session.commit()
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
    from app.services.sniper import snipe_scan, stop_loss_scan
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
    logger.info(
        "Starting scheduler with agent cycle every %ss (first run NOW)",
        settings.agent_scan_interval_seconds,
    )
    scheduler.start()
