from datetime import datetime, date
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.bayse_client import BayseClient
from app.models.signal import Signal


async def execute_signal(session: AsyncSession, client: BayseClient, signal: Signal):
    # only BUY_YES/BUY_NO supported for now
    side = "BUY"
    outcome = "YES" if signal.signal_type in ("BUY_YES", "BUY", "YES") else "NO"
    amount = signal.suggested_stake

    resp = await client.place_order(event_id=signal.market_id, market_id=signal.market_id, side=side, outcome=outcome, amount=amount, currency=client.default_currency)
    signal.executed_order_id = resp.get("ammOrder", {}).get("id") or resp.get("clobOrder", {}).get("id") or resp.get("engine")
    signal.status = "EXECUTED"
    signal.executed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(signal)
    return resp


async def executed_today(session: AsyncSession) -> int:
    today = date.today()
    result = await session.execute(
        select(func.count()).select_from(Signal).where(
            Signal.executed_at.isnot(None),
            func.date(Signal.executed_at) == today,
        )
    )
    return result.scalar_one()
