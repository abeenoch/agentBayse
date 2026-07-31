import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.event_market import EventMarket
from app.models.trade import Trade
from app.services.execution_control import (
    execute_signal_with_controls,
    market_execution_exists,
    mark_market_completed,
)


class FakeClient:
    default_currency = "NGN"

    async def get_portfolio(self):
        return {"portfolioCurrentValue": 10_000, "outcomeBalances": []}

    async def get_wallet_balance(self):
        return 10_000.0


@pytest.mark.asyncio
async def test_market_execution_exists_detects_trade_and_completed_marker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            event_id="event-1",
            market_id="m1",
            market_name="Test",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=0.55,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="EXECUTED",
        )
        session.add(sig)
        await session.commit()
        await session.refresh(sig)

        trade = Trade(
            market_id="m1",
            market_name="Test",
            side="BUY",
            shares=10,
            price=0.55,
            total_cost=100.0,
            status="EXECUTED",
            signal_id=sig.id,
            bayse_order_id="11111111-1111-1111-1111-111111111111",
        )
        session.add(trade)
        await session.commit()

        assert await market_execution_exists(session, event_id="event-1", market_id="m1")


@pytest.mark.asyncio
async def test_mark_market_completed_persists_duplicate_suppression():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            event_id="event-2",
            market_id="m2",
            market_name="Test",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=0.55,
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

        assert not await market_execution_exists(session, event_id="event-2", market_id="m2")

        await mark_market_completed(session, event_id="event-2", market_id="m2")

        assert await market_execution_exists(session, event_id="event-2", market_id="m2")


@pytest.mark.asyncio
async def test_guarded_execution_skips_second_attempt(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            event_id="event-3",
            market_id="m3",
            market_name="Test",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=0.55,
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

        calls = {"count": 0}

        async def fake_execute_signal(*args, **kwargs):
            calls["count"] += 1

        monkeypatch.setattr("app.services.execution_control.execute_signal", fake_execute_signal)

        client = FakeClient()
        assert await execute_signal_with_controls(session, client, sig)
        assert calls["count"] == 1

        # Second attempt should be skipped because the event/market is now marked completed.
        assert not await execute_signal_with_controls(session, client, sig)
        assert calls["count"] == 1


@pytest.mark.asyncio
async def test_pending_event_market_does_not_block_first_execution(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            event_id="event-4",
            market_id="m4",
            market_name="Test",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=0.55,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="PENDING",
        )
        session.add(sig)
        session.add(
            EventMarket(
                event_id="event-4",
                market_id="m4",
                status="PENDING",
            )
        )
        await session.commit()
        await session.refresh(sig)

        calls = {"count": 0}

        async def fake_execute_signal(*args, **kwargs):
            calls["count"] += 1

        monkeypatch.setattr("app.services.execution_control.execute_signal", fake_execute_signal)

        client = FakeClient()
        assert await execute_signal_with_controls(session, client, sig)
        assert calls["count"] == 1
