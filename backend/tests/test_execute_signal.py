import pytest
import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.signal import Signal
from app.services.trade_executor import execute_signal
from app.database import Base


class FakeClient:
    default_currency = "NGN"

    async def place_order(self, event_id: str, market_id: str, side: str, outcome: str, amount: float, currency: str = "NGN"):
        return {"engine": "AMM", "ammOrder": {"id": "mock-order"}}


@pytest.mark.asyncio
async def test_execute_signal_marks_executed():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            market_id="m1",
            market_name="Test",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=50,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="PENDING",
        )
        session.add(sig)
        await session.commit()
        await session.refresh(sig)

        client = FakeClient()
        await execute_signal(session, client, sig)
        assert sig.status == "EXECUTED"
        assert sig.executed_order_id == "mock-order"
        assert sig.executed_at is not None
