from typing import List, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.models.signal import Signal as SignalModel
from app.models.trade import Trade as TradeModel


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
        status="PENDING",
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
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
        # Active signals only: live bets (EXECUTED) and ones awaiting manual approval (PENDING)
        # Exclude resolved (WON/LOST/SOLD/STALE) and anything older than 24h
        cutoff = datetime.utcnow() - timedelta(hours=24)
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
        SignalModel.rank_score.desc().nullslast(), SignalModel.created_at.desc()
    ).limit(limit).offset((page - 1) * limit)
    result = await session.execute(query)
    return list(result.scalars().all()), total


async def clear_signals(session: AsyncSession) -> int:
    count_result = await session.execute(select(func.count()).select_from(SignalModel))
    total = count_result.scalar_one()
    await session.execute(delete(SignalModel))
    await session.commit()
    return total
