from fastapi import APIRouter, Depends, HTTPException
import uuid

from app.config import settings
from app.services.ai_agent import get_agent, AIAgent
from app.services.storage import (
    list_signals,
    clear_signals,
    list_feature_snapshots,
    feature_snapshot_metrics,
    get_bayes_state,
)
from app.services.outcome_sync import rebuild_bayes_state_from_resolved_trades
from app.services.bayes_backtest import (
    build_yes_no_audit,
)
from app.services.bayes_training import (
    build_calibration_audit,
    build_offline_eval_report,
    get_latest_training_run,
    resolve_live_training_run,
    train_bayes_model,
)
from app.database import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.signal import Signal
from app.models.trade import Trade
from app.models.feature_snapshot import FeatureSnapshot
from app.services.bayse_client import get_bayse_client
from app.services.config_service import get_config, update_config
from app.services.scheduler import collect_live_trade_diagnostics
from app.services.scheduler import normalize_terminal_trades
from app.dependencies import get_current_user
from app.services.execution_control import execute_signal_with_controls
from app.services.confidence_calibration import compute_calibration, calibration_to_dict
from app.websocket_manager import manager
from sqlalchemy import select

router = APIRouter(dependencies=[Depends(get_current_user)])


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


@router.get("/bayes/snapshots")
async def bayes_snapshots(
    limit: int = 20,
    page: int = 1,
    market_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    limit = max(1, min(limit, 100))
    page = max(1, page)
    snapshots, total = await list_feature_snapshots(session, limit=limit, page=page, market_id=market_id)
    serialized = []
    for s in snapshots:
        data = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
        for k, v in list(data.items()):
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        serialized.append(data)
    return {"snapshots": serialized, "page": page, "size": limit, "count": total, "total": total}


async def _resolve_active_bayes_key(session: AsyncSession, explicit: str | None = None) -> str:
    """
    Resolve the Bayes state key the dashboard/tools should operate on.

    Honors an explicit override. Otherwise it follows the live pipeline: the key
    of the most recently placed bet, so the report/live-training view tracks the
    last bet's coin (ETH/BTC/SOL) and changes as new bets land. Falls back to the
    configured base key before any bets exist.
    """
    explicit_key = (explicit or "").strip()
    if explicit_key:
        return explicit_key

    last = await session.execute(
        select(Signal.bayes_state_key)
        .where(Signal.executed_at.isnot(None), Signal.bayes_state_key.isnot(None))
        .order_by(Signal.executed_at.desc())
        .limit(1)
    )
    row = last.first()
    if row and (row[0] or "").strip():
        return row[0].strip()

    cfg = await get_config(session)
    configured = (getattr(cfg, "bayes_state_key", None) or "").strip()
    if configured:
        return configured
    return settings.bayes_state_key or "default"


@router.get("/bayes/report")
async def bayes_report(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    metrics = await feature_snapshot_metrics(session)
    resolved_state_key = await _resolve_active_bayes_key(session, explicit=state_key)
    state = await get_bayes_state(session, state_key=resolved_state_key)
    live_training_run, resolved_live_training_state_key = await resolve_live_training_run(
        session,
        state_key=resolved_state_key,
        default_key=settings.bayes_state_key,
    )
    metrics["bayes_state"] = None if state is None else {
        "state_key": state.state_key,
        "model_version": state.model_version,
        "prior_json": state.prior_json,
        "parameter_json": state.parameter_json,
        "calibration_json": state.calibration_json,
        "yes_updates": state.yes_updates,
        "no_updates": state.no_updates,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }
    metrics["live_training_run"] = None if live_training_run is None else {
        "id": str(live_training_run.id),
        "state_key": live_training_run.state_key,
        "model_version": live_training_run.model_version,
        "sample_size": live_training_run.sample_size,
        "train_size": live_training_run.train_size,
        "test_size": live_training_run.test_size,
        "positive_rate": live_training_run.positive_rate,
        "trained_at": live_training_run.trained_at.isoformat() if live_training_run.trained_at else None,
    }
    metrics["resolved_live_training_state_key"] = resolved_live_training_state_key
    return metrics


@router.post("/bayes/rebuild")
async def rebuild_bayes_state(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    return await rebuild_bayes_state_from_resolved_trades(session, state_key=key)


@router.get("/bayes/audit")
async def bayes_audit(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    return await build_yes_no_audit(session, state_key=key)


@router.get("/bayes/calibration")
async def bayes_calibration(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    return await build_calibration_audit(session, state_key=key)


@router.post("/bayes/train")
async def bayes_train(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    return await train_bayes_model(session, state_key=key)


@router.get("/bayes/eval")
async def bayes_eval(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    return await build_offline_eval_report(session, state_key=key)


@router.get("/bayes/train/latest")
async def bayes_train_latest(
    state_key: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    key = await _resolve_active_bayes_key(session, explicit=state_key)
    run = await get_latest_training_run(session, state_key=key)
    if run is None:
        return None
    return {
        "id": str(run.id),
        "state_key": run.state_key,
        "model_version": run.model_version,
        "sample_size": run.sample_size,
        "train_size": run.train_size,
        "test_size": run.test_size,
        "positive_rate": run.positive_rate,
        "feature_names": run.feature_names,
        "coefficients": run.coefficients,
        "metrics": run.metrics_json,
        "calibration": run.calibration_json,
        "trained_at": run.trained_at.isoformat() if run.trained_at else None,
    }


@router.get("/bayes/live-training")
async def bayes_live_training(
    session: AsyncSession = Depends(get_session),
):
    live_training_run, resolved_state_key = await resolve_live_training_run(
        session,
        state_key=await _resolve_active_bayes_key(session),
        default_key=settings.bayes_state_key,
    )
    if live_training_run is None:
        return {
            "resolved_state_key": resolved_state_key,
            "live_training_run": None,
        }
    return {
        "resolved_state_key": resolved_state_key,
        "live_training_run": {
            "id": str(live_training_run.id),
            "state_key": live_training_run.state_key,
            "model_version": live_training_run.model_version,
            "sample_size": live_training_run.sample_size,
            "train_size": live_training_run.train_size,
            "test_size": live_training_run.test_size,
            "positive_rate": live_training_run.positive_rate,
            "trained_at": live_training_run.trained_at.isoformat() if live_training_run.trained_at else None,
        },
    }


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
    executed = await execute_signal_with_controls(session, client, signal, amount_override=amount)
    if not executed:
        return {"status": "skipped"}
    return {"status": "executed", "order_id": signal.executed_order_id}

@router.post("/trades/clear-stale")
async def clear_stale_trades(
    session: AsyncSession = Depends(get_session),
):
    """Mark unresolved or terminal-expired EXECUTED trades as STALE."""
    from sqlalchemy import update
    from app.models.trade import Trade
    result = await session.execute(
        update(Trade)
        .where(
            Trade.status == "EXECUTED",
            Trade.resolution.in_([None, "EXPIRED"]),
        )
        .values(status="STALE", resolution="EXPIRED")
    )
    await session.commit()
    return {"cleared": result.rowcount}


@router.post("/trades/repair-terminal")
async def repair_terminal_trades(
    session: AsyncSession = Depends(get_session),
):
    """
    Normalize terminal-but-skipped trades so reconciliation can process them.

    This is a narrow repair for rows left in EXECUTED/EXPIRED state after a
    restart or partial sync.
    """
    from sqlalchemy import select
    from app.models.trade import Trade

    result = await session.execute(
        select(Trade.id).where(Trade.status == "EXECUTED", Trade.resolution == "EXPIRED")
    )
    trade_ids = [row[0] for row in result.all()]
    normalized = await normalize_terminal_trades(session)
    if normalized:
        await session.commit()
    return {"normalized": normalized, "trade_ids": [str(tid) for tid in trade_ids]}


@router.get("/trades/diagnostics")
async def live_trade_diagnostics(
    market_id: str | None = None,
    include_stale_expired: bool = False,
    session: AsyncSession = Depends(get_session),
    client=Depends(get_bayse_client),
):
    diagnostics = await collect_live_trade_diagnostics(
        session,
        client,
        include_stale_expired=include_stale_expired,
    )
    if market_id:
        diagnostics = [row for row in diagnostics if row.get("market_id") == market_id]
    return {
        "count": len(diagnostics),
        "trades": diagnostics,
    }


@router.get("/trades/trace")
async def trade_pipeline_trace(
    market_id: str | None = None,
    trade_id: str | None = None,
    include_diagnostics: bool = False,
    session: AsyncSession = Depends(get_session),
    client=Depends(get_bayse_client),
):
    """
    Return a compact end-to-end trace for one market or trade.

    This is intended for debugging settlement, stop-loss, and Bayes updates.
    """
    if not market_id and not trade_id:
        raise HTTPException(status_code=400, detail="Provide market_id or trade_id")

    trade = None
    if trade_id:
        try:
            parsed_trade_id = uuid.UUID(trade_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid trade_id format")
        trade = await session.get(Trade, parsed_trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        market_id = market_id or trade.market_id

    signal = None
    if market_id:
        signal_result = await session.execute(
            select(Signal).where(Signal.market_id == market_id).order_by(Signal.created_at.desc()).limit(1)
        )
        signal = signal_result.scalars().first()

    if trade is None and market_id:
        trade_result = await session.execute(
            select(Trade).where(Trade.market_id == market_id).order_by(Trade.created_at.desc()).limit(1)
        )
        trade = trade_result.scalars().first()

    snapshot = None
    if market_id:
        snapshot_result = await session.execute(
            select(FeatureSnapshot)
            .where(FeatureSnapshot.market_id == market_id)
            .order_by(FeatureSnapshot.created_at.desc())
            .limit(1)
        )
        snapshot = snapshot_result.scalars().first()

    cfg = await get_config(session)
    trace_state_key = (
        getattr(trade, "bayes_state_key", None)
        or getattr(signal, "bayes_state_key", None)
        or getattr(cfg, "bayes_state_key", "default")
        or "default"
    )
    bayes_state = await get_bayes_state(session, state_key=trace_state_key)
    live_training_run, resolved_training_state_key = await resolve_live_training_run(
        session,
        state_key=trace_state_key,
        default_key=settings.bayes_state_key,
    )
    live_diagnostics = []
    if include_diagnostics:
        live_diagnostics = await collect_live_trade_diagnostics(
            session,
            client,
            include_stale_expired=True,
        )
        if market_id:
            live_diagnostics = [row for row in live_diagnostics if row.get("market_id") == market_id]

    def _serialize(obj):
        if obj is None:
            return None
        data = {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        for k, v in list(data.items()):
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
        return data

    return {
        "market_id": market_id,
        "trade": _serialize(trade),
        "signal": _serialize(signal),
        "feature_snapshot": _serialize(snapshot),
        "bayes_state": None if bayes_state is None else {
            "state_key": bayes_state.state_key,
            "model_version": bayes_state.model_version,
            "prior_json": bayes_state.prior_json,
            "parameter_json": bayes_state.parameter_json,
            "calibration_json": bayes_state.calibration_json,
            "yes_updates": bayes_state.yes_updates,
            "no_updates": bayes_state.no_updates,
            "updated_at": bayes_state.updated_at.isoformat() if bayes_state.updated_at else None,
        },
        "live_training_run": None if live_training_run is None else {
            "id": str(live_training_run.id),
            "state_key": live_training_run.state_key,
            "model_version": live_training_run.model_version,
            "sample_size": live_training_run.sample_size,
            "train_size": live_training_run.train_size,
            "test_size": live_training_run.test_size,
            "positive_rate": live_training_run.positive_rate,
            "trained_at": live_training_run.trained_at.isoformat() if live_training_run.trained_at else None,
        },
        "resolved_live_training_state_key": resolved_training_state_key,
        "resolved_bayes_state_key": trace_state_key,
        "config": {
            "auto_trade": cfg.auto_trade,
            "max_open_positions": cfg.max_open_positions,
            "min_confidence": cfg.min_confidence,
            "balance_reserve_pct": getattr(cfg, "balance_reserve_pct", 0.30),
            "bayes_live_decision_mode": getattr(cfg, "bayes_live_decision_mode", True),
            "bayes_state_key": getattr(cfg, "bayes_state_key", "default"),
        },
        "live_diagnostics": live_diagnostics,
    }


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
        "max_open_positions": cfg.max_open_positions,
        "balance_floor": cfg.balance_floor,
        "min_confidence": cfg.min_confidence,
        "balance_reserve_pct": getattr(cfg, "balance_reserve_pct", 0.30),
        "bayes_live_decision_mode": getattr(cfg, "bayes_live_decision_mode", True),
        "bayes_state_key": getattr(cfg, "bayes_state_key", "default"),
    }


@router.post("/config")
async def write_config(payload: dict, session: AsyncSession = Depends(get_session)):
    current_cfg = await get_config(session)
    cfg = await update_config(session, payload)
    mode_changed = (
        getattr(current_cfg, "bayes_live_decision_mode", None) != getattr(cfg, "bayes_live_decision_mode", None)
        or getattr(current_cfg, "bayes_state_key", None) != getattr(cfg, "bayes_state_key", None)
    )
    if mode_changed:
        await manager.broadcast({
            "type": "decision_mode_changed",
            "data": {
                "bayes_live_decision_mode": getattr(cfg, "bayes_live_decision_mode", True),
                "bayes_state_key": getattr(cfg, "bayes_state_key", "default"),
            },
        })
    return {
        "auto_trade": cfg.auto_trade,
        "categories": cfg.categories or [],
        "max_trades_per_hour": cfg.max_trades_per_hour,
        "max_trades_per_day": cfg.max_trades_per_day,
        "max_open_positions": cfg.max_open_positions,
        "balance_floor": cfg.balance_floor,
        "min_confidence": cfg.min_confidence,
        "balance_reserve_pct": getattr(cfg, "balance_reserve_pct", 0.30),
        "bayes_live_decision_mode": getattr(cfg, "bayes_live_decision_mode", True),
        "bayes_state_key": getattr(cfg, "bayes_state_key", "default"),
    }
@router.get("/calibration")
async def calibration_report(
    session: AsyncSession = Depends(get_session),
):
    """
    Compute LLM confidence calibration from resolved signals.
    Returns calibration bins showing actual win rate vs predicted confidence.
    """
    report = await compute_calibration(session)
    return calibration_to_dict(report)
