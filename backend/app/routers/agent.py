from fastapi import APIRouter, Depends, HTTPException

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
    session: AsyncSession = Depends(get_session),
):
    signals = await list_signals(session, limit=limit)
    serialized = []
    for s in signals:
        data = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        for k, v in list(data.items()):
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        serialized.append(data)
    # Sort newest-first before returning
    serialized.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return serialized


@router.post("/approve")
async def approve_signal(
    signal_id: str,
    session: AsyncSession = Depends(get_session),
    client=Depends(get_bayse_client),
):
    signal: Signal | None = await session.get(Signal, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status == "EXECUTED":
        return {"status": "already_executed", "order_id": signal.executed_order_id}
    await execute_signal(session, client, signal)
    return {"status": "executed", "order_id": signal.executed_order_id}


@router.post("/signals/clear")
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
        "balance_floor": cfg.balance_floor,
    }


@router.post("/config")
async def write_config(payload: dict, session: AsyncSession = Depends(get_session)):
    cfg = await update_config(session, payload)
    return {
        "auto_trade": cfg.auto_trade,
        "categories": cfg.categories or [],
        "max_trades_per_hour": cfg.max_trades_per_hour,
        "max_trades_per_day": cfg.max_trades_per_day,
        "balance_floor": cfg.balance_floor,
    }
