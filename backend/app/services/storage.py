from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.signal import Signal as SignalModel
from app.models.trade import Trade as TradeModel


async def save_signal(session: AsyncSession, payload: dict) -> SignalModel:
    obj = SignalModel(
        market_id=payload["market_id"],
        market_name=payload["market_name"],
        signal_type=payload["signal"],
        confidence=payload["confidence"],
        estimated_probability=payload["estimated_probability"],
        market_price_at_signal=payload["current_market_price"],
        expected_value=payload["expected_value"],
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


async def list_signals(session: AsyncSession, limit: int = 20) -> List[SignalModel]:
    result = await session.execute(select(SignalModel).order_by(SignalModel.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def clear_signals(session: AsyncSession) -> int:
    result = await session.execute(select(SignalModel))
    signals = list(result.scalars().all())
    deleted = len(signals)
    for s in signals:
        await session.delete(s)
    await session.commit()
    return deleted
