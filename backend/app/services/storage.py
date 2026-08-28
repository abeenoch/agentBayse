from typing import List, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.models.signal import Signal as SignalModel
from app.models.trade import Trade as TradeModel
from app.models.market_snapshot import MarketSnapshot as MarketSnapshotModel
from app.models.feature_snapshot import FeatureSnapshot as FeatureSnapshotModel
from app.models.portfolio_snapshot import PortfolioSnapshot as PortfolioSnapshotModel
from app.models.bayes_state import BayesState as BayesStateModel
from app.config import settings
from app.services.feature_encoder import FeatureEncoding
from app.services.bayes_model import BayesPosterior, BayesState
from app.websocket_manager import manager


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


async def save_signal(session: AsyncSession, payload: dict) -> SignalModel:
    obj = SignalModel(
        event_id=payload.get("event_id") or "",
        market_id=payload["market_id"],
        market_name=payload["market_name"],
        signal_type=payload["signal"],
        confidence=payload["confidence"],
        estimated_probability=payload["estimated_probability"],
        market_price_at_signal=payload["current_market_price"],
        expected_value=payload["expected_value"],
        rank_score=payload.get("rank_score"),
        reasoning=payload["reasoning"],
        sources=payload.get("sources", []),
        suggested_stake=payload.get("suggested_stake", 0),
        risk_level=payload.get("risk_level", "MEDIUM"),
        bayes_state_key=payload.get("bayes_state_key") or "default",
        status="PENDING",
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def save_feature_snapshot(
    session: AsyncSession,
    *,
    market_id: str,
    market_name: str,
    feature: FeatureEncoding,
    posterior: BayesPosterior | None = None,
    event_id: str | None = None,
    signal_id=None,
    trade_id=None,
    source: str = "agent",
) -> FeatureSnapshotModel:
    obj = FeatureSnapshotModel(
        market_id=market_id,
        market_name=market_name,
        event_id=event_id,
        schema_version=feature.schema_version,
        feature_json=feature.model_dump(mode="json"),
        semantic_vector=feature.semantic_vector,
        market_vector=feature.market_vector,
        portfolio_vector=feature.portfolio_vector,
        cross_market_vector=feature.cross_market_vector,
        posterior_yes=posterior.posterior_yes if posterior else None,
        posterior_no=posterior.posterior_no if posterior else None,
        posterior_action=posterior.suggested_action if posterior else None,
        posterior_confidence=posterior.model_confidence if posterior else None,
        model_version=(posterior.model_version if posterior else "v1"),
        source=source,
        resolved_signal_id=signal_id,
        resolved_trade_id=trade_id,
        vector_size_semantic=len(feature.semantic_vector),
        vector_size_market=len(feature.market_vector),
        vector_size_portfolio=len(feature.portfolio_vector),
        vector_size_cross_market=len(feature.cross_market_vector),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    await manager.broadcast({
        "type": "bayes_snapshot",
        "data": {
            "id": str(obj.id),
            "market_id": obj.market_id,
            "market_name": obj.market_name,
            "posterior_action": obj.posterior_action,
            "posterior_yes": obj.posterior_yes,
            "posterior_no": obj.posterior_no,
            "posterior_confidence": obj.posterior_confidence,
            "created_at": obj.created_at.isoformat() if obj.created_at else None,
        },
    })
    return obj


async def link_feature_snapshot(
    session: AsyncSession,
    *,
    market_id: str,
    signal_id=None,
    trade_id=None,
    feature_snapshot_id=None,
) -> FeatureSnapshotModel | None:
    """
    Attach live decision or resolution IDs to the most recent matching feature snapshot.

    This keeps the pre-trade snapshot row connected to the signal that created it and the
    trade that eventually resolved from it, which makes the row reusable for inspection
    and offline training/backfill workflows.
    """
    query = select(FeatureSnapshotModel).where(FeatureSnapshotModel.market_id == market_id)
    if feature_snapshot_id is not None:
        query = query.where(FeatureSnapshotModel.id == feature_snapshot_id)
    else:
        if signal_id is not None:
            query = query.where(
                (FeatureSnapshotModel.resolved_signal_id.is_(None))
                | (FeatureSnapshotModel.resolved_signal_id == signal_id)
            )
        if trade_id is not None:
            query = query.where(
                (FeatureSnapshotModel.resolved_trade_id.is_(None))
                | (FeatureSnapshotModel.resolved_trade_id == trade_id)
            )
    query = query.order_by(FeatureSnapshotModel.created_at.desc())

    result = await session.execute(query)
    snapshot = result.scalars().first()
    if snapshot is None:
        return None

    dirty = False
    if signal_id is not None and snapshot.resolved_signal_id != signal_id:
        snapshot.resolved_signal_id = signal_id
        dirty = True
    if trade_id is not None and snapshot.resolved_trade_id != trade_id:
        snapshot.resolved_trade_id = trade_id
        dirty = True

    if dirty:
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
    return snapshot


async def save_market_snapshot(
    session: AsyncSession,
    *,
    market_id: str,
    event_id: str,
    title: str,
    market: dict,
) -> MarketSnapshotModel:
    obj = MarketSnapshotModel(
        market_id=market_id,
        event_id=event_id,
        title=title,
        outcome1_label=market.get("outcome1Label") or market.get("yesLabel") or market.get("outcome1"),
        outcome1_price=market.get("outcome1Price"),
        outcome2_label=market.get("outcome2Label") or market.get("noLabel") or market.get("outcome2"),
        outcome2_price=market.get("outcome2Price"),
        liquidity=market.get("liquidity"),
        total_volume=market.get("totalVolume") or market.get("volume"),
        total_orders=market.get("totalOrders"),
        raw=_json_safe(market),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def save_portfolio_snapshot(
    session: AsyncSession,
    *,
    total_balance: float | None = None,
    invested: float | None = None,
    unrealized_pnl: float | None = None,
    realized_pnl_today: float | None = None,
    positions_count: int | None = None,
    snapshot_data: dict | None = None,
) -> PortfolioSnapshotModel:
    obj = PortfolioSnapshotModel(
        total_balance=total_balance,
        invested=invested,
        unrealized_pnl=unrealized_pnl,
        realized_pnl_today=realized_pnl_today,
        positions_count=positions_count,
        snapshot_data=_json_safe(snapshot_data or {}),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def get_bayes_state(session: AsyncSession, state_key: str = "default") -> BayesStateModel | None:
    result = await session.execute(select(BayesStateModel).where(BayesStateModel.state_key == state_key))
    return result.scalar_one_or_none()


async def ensure_bayes_state(
    session: AsyncSession,
    *,
    state_key: str,
    alpha: float = 1.0,
    beta: float = 1.0,
) -> BayesStateModel:
    """
    Idempotently create a Bayes state row with a neutral prior.

    alpha=1, beta=1 gives a flat 50/50 prior_yes. Existing rows are never
    overwritten, so real learning always wins over the seed.
    """
    existing = await get_bayes_state(session, state_key=state_key)
    if existing is not None:
        return existing
    total = alpha + beta
    prior = (alpha / total) if total else 0.5
    obj = BayesStateModel(
        state_key=state_key,
        model_version="v1",
        prior_json={"alpha": alpha, "beta": beta, "prior_yes": prior},
        parameter_json={"alpha": alpha, "beta": beta, "yes_updates": 0, "no_updates": 0},
        yes_updates=0,
        no_updates=0,
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


def crypto_bayes_state_keys() -> list[str]:
    """State keys to pre-seed with a neutral 50/50 prior for crypto series."""
    keys: list[str] = []
    for raw in (
        getattr(settings, "agent_series_slugs", ""),
        getattr(settings, "snipe_series_slugs", ""),
    ):
        for slug in [s.strip() for s in str(raw).split(",") if s.strip()]:
            slug_lower = slug.lower()
            if slug_lower.startswith("crypto-"):
                key = f"series:{slug_lower}"
                if key not in keys:
                    keys.append(key)
    if "crypto" not in keys:
        keys.append("crypto")
    if "category:crypto" not in keys:
        keys.append("category:crypto")
    return keys


async def list_feature_snapshots(
    session: AsyncSession,
    limit: int = 50,
    page: int = 1,
    market_id: str | None = None,
):
    base_query = select(FeatureSnapshotModel)
    if market_id:
        base_query = base_query.where(FeatureSnapshotModel.market_id == market_id)
    total_result = await session.execute(select(func.count()).select_from(base_query.subquery()))
    total = total_result.scalar_one()
    query = (
        base_query.order_by(FeatureSnapshotModel.created_at.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    )
    result = await session.execute(query)
    return list(result.scalars().all()), total


async def feature_snapshot_metrics(session: AsyncSession) -> dict:
    total_result = await session.execute(select(func.count()).select_from(FeatureSnapshotModel))
    total = total_result.scalar_one()
    posterior_yes_avg = await session.execute(select(func.avg(FeatureSnapshotModel.posterior_yes)))
    posterior_no_avg = await session.execute(select(func.avg(FeatureSnapshotModel.posterior_no)))
    resolved_signal_result = await session.execute(
        select(func.count()).select_from(FeatureSnapshotModel).where(FeatureSnapshotModel.resolved_signal_id.isnot(None))
    )
    resolved_trade_result = await session.execute(
        select(func.count()).select_from(FeatureSnapshotModel).where(FeatureSnapshotModel.resolved_trade_id.isnot(None))
    )
    linked_result = await session.execute(
        select(func.count()).select_from(FeatureSnapshotModel).where(
            FeatureSnapshotModel.resolved_signal_id.isnot(None),
            FeatureSnapshotModel.resolved_trade_id.isnot(None),
        )
    )
    resolved_signal_count = int(resolved_signal_result.scalar() or 0)
    resolved_trade_count = int(resolved_trade_result.scalar() or 0)
    linked_count = int(linked_result.scalar() or 0)
    action_counts = await session.execute(
        select(FeatureSnapshotModel.posterior_action, func.count())
        .group_by(FeatureSnapshotModel.posterior_action)
        .order_by(func.count().desc())
    )
    return {
        "total_snapshots": total,
        "resolved_signal_snapshots": resolved_signal_count,
        "resolved_trade_snapshots": resolved_trade_count,
        "fully_linked_snapshots": linked_count,
        "unlinked_snapshots": max(int(total) - linked_count, 0),
        "avg_posterior_yes": float(posterior_yes_avg.scalar() or 0.0),
        "avg_posterior_no": float(posterior_no_avg.scalar() or 0.0),
        "action_counts": [
            {"action": row[0], "count": int(row[1])}
            for row in action_counts.all()
        ],
    }


async def save_bayes_state(
    session: AsyncSession,
    state: BayesState,
    *,
    state_key: str = "default",
    calibration_json: dict | None = None,
) -> BayesStateModel:
    obj = await get_bayes_state(session, state_key=state_key)
    if obj is None:
        obj = BayesStateModel(state_key=state_key)
    obj.model_version = state.model_version
    obj.prior_json = {"alpha": state.alpha, "beta": state.beta, "prior_yes": state.prior_yes}
    obj.parameter_json = {
        "alpha": state.alpha,
        "beta": state.beta,
        "yes_updates": state.yes_updates,
        "no_updates": state.no_updates,
    }
    obj.calibration_json = calibration_json or obj.calibration_json
    obj.yes_updates = state.yes_updates
    obj.no_updates = state.no_updates
    obj.updated_at = state.updated_at
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    await manager.broadcast({
        "type": "bayes_state",
        "data": {
            "state_key": obj.state_key,
            "model_version": obj.model_version,
            "yes_updates": obj.yes_updates,
            "no_updates": obj.no_updates,
            "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        },
    })
    return obj


async def list_signals(
    session: AsyncSession,
    limit: int = 20,
    page: int = 1,
    event_id: str | None = None,
    actionable_only: bool = True,
) -> Tuple[List[SignalModel], int]:
    base_query = select(SignalModel)

    if event_id:
        base_query = base_query.where(SignalModel.event_id == event_id)

    if actionable_only:
        # Active signals: only show recent ones (last 3 hours) that are still live
        cutoff = datetime.utcnow() - timedelta(hours=3)
        base_query = base_query.where(
            SignalModel.signal_type.in_(["BUY_YES", "BUY_NO"]),
            SignalModel.status.in_(["PENDING", "EXECUTED"]),
            SignalModel.created_at >= cutoff,
        )
    else:
        # Full history — just exclude HOLD/AVOID noise
        base_query = base_query.where(
            SignalModel.signal_type.in_(["BUY_YES", "BUY_NO"]),
        )

    total_result = await session.execute(select(func.count()).select_from(base_query.subquery()))
    total = total_result.scalar_one()

    query = base_query.order_by(
        SignalModel.created_at.desc()
    ).limit(limit).offset((page - 1) * limit)
    result = await session.execute(query)
    return list(result.scalars().all()), total


async def clear_signals(session: AsyncSession) -> int:
    count_result = await session.execute(select(func.count()).select_from(SignalModel))
    total = count_result.scalar_one()
    await session.execute(delete(SignalModel))
    await session.commit()
    return total
