from fastapi import APIRouter, Depends, HTTPException
import uuid

from app.services.ai_agent import get_agent, AIAgent
from app.services.storage import list_signals, clear_signals
from app.database import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.trade_executor import execute_signal
from app.models.signal import Signal
from app.services.bayse_client import get_bayse_client
from app.services.config_service import get_config, update_config

router = APIRouter()


@router.post("/analyze")
async def analyze_market(
    event_id: str,
    market_id: str | None = None,
    agent: AIAgent = Depends(get_agent),
    session: AsyncSession = Depends(get_session),
):
    event = await agent.bayse.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    target_market_id = market_id
    if not target_market_id and event.get("markets"):
        target_market_id = event["markets"][0]["id"]
    if not target_market_id:
        raise HTTPException(status_code=400, detail="No market supplied or found")
    signal = await agent.analyze_market(target_market_id, event=event, session=session)
    if not signal:
        raise HTTPException(status_code=400, detail="No viable signal generated")
    return signal.__dict__


@router.get("/signals")
async def latest_signals(
    limit: int = 20,
    page: int = 1,
    event_id: str | None = None,
    all: bool = False,
    session: AsyncSession = Depends(get_session),
):
    limit = max(1, min(limit, 100))
    page = max(1, page)
    # By default only return BUY signals from last 24h — pass ?all=true to see everything
    signals, total = await list_signals(
        session, limit=limit, page=page, event_id=event_id, actionable_only=not all
    )
    serialized = []
    for s in signals:
        data = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        for k, v in list(data.items()):
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        serialized.append(data)
    return {"signals": serialized, "page": page, "size": limit, "count": total, "total": total}


@router.post("/approve")
async def approve_signal(
    signal_id: str,
    amount: float | None = None,
    session: AsyncSession = Depends(get_session),
    client=Depends(get_bayse_client),
):
    try:
        parsed_id = uuid.UUID(signal_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid signal_id format")
    signal: Signal | None = await session.get(Signal, parsed_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status == "EXECUTED":
        return {"status": "already_executed", "order_id": signal.executed_order_id}
    await execute_signal(session, client, signal, amount_override=amount)
    return {"status": "executed", "order_id": signal.executed_order_id}

@router.post("/trades/clear-stale")
async def clear_stale_trades(
    session: AsyncSession = Depends(get_session),
):
    """Mark all EXECUTED trades with no resolution as STALE. Use when DB is out of sync with Bayse."""
    from sqlalchemy import update
    from app.models.trade import Trade
    result = await session.execute(
        update(Trade)
        .where(Trade.status == "EXECUTED", Trade.resolution.is_(None))
        .values(status="STALE", resolution="EXPIRED")
    )
    await session.commit()
    return {"cleared": result.rowcount}


async def clear_all_signals(
    session: AsyncSession = Depends(get_session),
):
    deleted = await clear_signals(session)
    return {"deleted": deleted}


@router.get("/status")
async def status():
    return {"status": "idle"}


@router.get("/config")
async def read_config(session: AsyncSession = Depends(get_session)):
    cfg = await get_config(session)
    return {
        "auto_trade": cfg.auto_trade,
        "categories": cfg.categories or [],
        "max_trades_per_hour": cfg.max_trades_per_hour,
        "max_trades_per_day": cfg.max_trades_per_day,
        "max_open_positions": cfg.max_open_positions,
        "balance_floor": cfg.balance_floor,
        "min_confidence": cfg.min_confidence,
        "balance_reserve_pct": getattr(cfg, "balance_reserve_pct", 0.30),
    }


@router.post("/config")
async def write_config(payload: dict, session: AsyncSession = Depends(get_session)):
    cfg = await update_config(session, payload)
    return {
        "auto_trade": cfg.auto_trade,
        "categories": cfg.categories or [],
        "max_trades_per_hour": cfg.max_trades_per_hour,
        "max_trades_per_day": cfg.max_trades_per_day,
        "max_open_positions": cfg.max_open_positions,
        "balance_floor": cfg.balance_floor,
        "min_confidence": cfg.min_confidence,
        "balance_reserve_pct": getattr(cfg, "balance_reserve_pct", 0.30),
    }
