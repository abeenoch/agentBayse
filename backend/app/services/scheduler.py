import asyncio
from datetime import datetime
from typing import List, Dict

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.services.ai_agent import get_agent
from app.services.bayse_client import get_bayse_client
from app.database import AsyncSessionLocal
from app.services.config_service import get_config
from app.services.risk_guard import check_trade_limits
from app.utils.logger import logger

scheduler = AsyncIOScheduler()
pending_markets: List[Dict] = []


async def populate_queue():
    client = get_bayse_client()
    events: List[Dict] = []
    page_size = settings.agent_event_page_size
    max_pages = settings.agent_event_pages
    cfg_categories = set()
    async with AsyncSessionLocal() as session:
        cfg = await get_config(session)
        cfg_categories = {c.lower() for c in (cfg.categories or [])}
    for page in range(1, max_pages + 1):
        result = await client.list_events(status="open", page=page, size=page_size)
        if not result:
            break
        events.extend(result.get("events", []))
        last_page = result.get("pagination", {}).get("lastPage") or page
        if page >= last_page:
            break
    if cfg_categories:
        events = [e for e in events if (e.get("category") or "").lower() in cfg_categories]
    for event in events:
        for market in event.get("markets", []) or []:
            pending_markets.append({"event": event, "market": market})
    logger.info("Queue populated with %d markets from %d events", len(pending_markets), len(events))


async def run_agent_cycle():
    try:
        if not pending_markets:
            await populate_queue()
        if not pending_markets:
            logger.info("No markets queued; skipping cycle")
            return
        logger.info("Processing %d queued markets", len(pending_markets))
        async with AsyncSessionLocal() as session:
            cfg = await get_config(session)
            if not cfg.auto_trade:
                logger.info("Auto-trade disabled; skipping cycle.")
                return
            agent = get_agent()
            while pending_markets:
                item = pending_markets.pop(0)
                event = item["event"]
                market = item["market"]
                try:
                    # enforce rate limits per cycle
                    tl = await check_trade_limits(session, cfg)
                    if not tl.passed:
                        logger.info("Trade cap reached; postponing remaining markets")
                        break
                    signal = await agent.analyze_market(market["id"], event=event, session=session)
                    if signal:
                        logger.info(
                            "Generated signal %s %s ev=%.2f",
                            signal.market_id,
                            signal.signal,
                            signal.expected_value,
                        )
                except Exception as market_err:
                    logger.error("Error analyzing market %s: %s", market.get("id"), market_err, exc_info=True)
    except Exception as exc:
        logger.error("Agent cycle failed: %s", exc, exc_info=True)


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
    logger.info(
        "Starting scheduler with agent cycle every %ss (first run NOW)",
        settings.agent_scan_interval_seconds,
    )
    scheduler.start()
