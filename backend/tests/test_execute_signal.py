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

    def __init__(self):
        self.last_amount = None

    async def get_event(self, event_id: str):
        return {"id": event_id, "markets": [{"id": "m1", "outcome1Id": "oid-yes", "outcome2Id": "oid-no"}]}

    async def place_order(
        self,
        event_id: str,
        market_id: str,
        side: str,
        amount: float,
        outcome: str | None = None,
        outcome_id: str | None = None,
        order_type: str = "MARKET",
        currency: str = "NGN",
        price: float | None = None,
    ):
        assert outcome_id == "oid-yes"
        self.last_amount = amount
        return {"engine": "AMM", "order": {"id": "mock-order", "quantity": 100, "price": 0.55}}


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


@pytest.mark.asyncio
async def test_execute_signal_raises_small_ngn_stake_to_minimum():
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
            suggested_stake=0.05,
            risk_level="LOW",
            status="PENDING",
        )
        session.add(sig)
        await session.commit()
        await session.refresh(sig)

        client = FakeClient()
        await execute_signal(session, client, sig)
        assert client.last_amount == 100.0
