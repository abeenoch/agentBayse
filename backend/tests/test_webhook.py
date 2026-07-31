import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.trade import Trade
from app.routers.webhook import apply_order_resolution
from app.routers.agent import repair_terminal_trades
from app.services.storage import get_bayes_state


@pytest.mark.asyncio
async def test_apply_order_resolution_updates_trade_signal_and_bayes(monkeypatch):
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
            market_price_at_signal=50,
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
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            signal_id=sig.id,
            bayse_order_id="33333333-3333-3333-3333-333333333333",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        async def fake_broadcast(*args, **kwargs):
            return None

        monkeypatch.setattr("app.routers.webhook.manager.broadcast", fake_broadcast)

        result = await apply_order_resolution(
            session,
            {
                "orderId": "33333333-3333-3333-3333-333333333333",
                "status": "RESOLVED",
                "resolution": "YES",
                "payout": "180",
            },
        )

        await session.refresh(sig)
        await session.refresh(trade)
        state = await get_bayes_state(session, state_key="default")

        assert result["matched"] is True
        assert trade.resolution == "WIN"
        assert trade.resolved_at is not None
        assert sig.resolution == "WIN"
        assert sig.status == "WON"
        assert sig.pnl == 80.0
        assert state is not None
        assert state.yes_updates == 1
        assert state.no_updates == 0


@pytest.mark.asyncio
async def test_apply_order_resolution_falls_back_to_event_market_match(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            event_id="event-2",
            market_id="m2",
            market_name="Test",
            signal_type="BUY_NO",
            confidence=70,
            estimated_probability=0.3,
            market_price_at_signal=40,
            expected_value=12,
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
            market_id="m2",
            market_name="Test",
            side="BUY",
            shares=10,
            price=0.4,
            total_cost=100.0,
            status="EXECUTED",
            signal_id=sig.id,
            bayse_order_id="44444444-4444-4444-4444-444444444444",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        async def fake_broadcast(*args, **kwargs):
            return None

        monkeypatch.setattr("app.routers.webhook.manager.broadcast", fake_broadcast)

        result = await apply_order_resolution(
            session,
            {
                "eventId": "event-2",
                "marketId": "m2",
                "status": "RESOLVED",
                "resolution": "YES",
                "payout": "60",
            },
        )

        await session.refresh(sig)
        await session.refresh(trade)
        state = await get_bayes_state(session, state_key="default")

        assert result["matched"] is True
        assert trade.resolution == "LOSS"
        assert trade.resolved_at is not None
        assert sig.resolution == "LOSS"
        assert sig.status == "LOST"
        assert sig.pnl == -40.0
        assert state is not None
        assert state.yes_updates == 1
        assert state.no_updates == 0


@pytest.mark.asyncio
async def test_repair_terminal_trades_normalizes_executed_expired():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        trade = Trade(
            market_id="m1",
            market_name="Test",
            side="BUY",
            shares=10,
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            resolution="EXPIRED",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        result = await repair_terminal_trades(session)
        await session.refresh(trade)

        assert result["normalized"] == 1
        assert trade.status == "STALE"
        assert trade.resolution == "EXPIRED"
