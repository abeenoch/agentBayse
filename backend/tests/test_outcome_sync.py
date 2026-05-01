import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.payout_reconciliation import index_payout_activities, match_payout_activity_for_trade
from app.services.outcome_sync import sync_signal_outcome


@pytest.mark.asyncio
async def test_sync_signal_outcome_marks_buy_yes_win():
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
            bayse_order_id="11111111-1111-1111-1111-111111111111",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        await sync_signal_outcome(session, trade, market_resolution="YES", payout=180.0)
        await session.commit()

        assert sig.resolution == "WIN"
        assert sig.status == "WON"
        assert sig.pnl == 80.0


@pytest.mark.asyncio
async def test_sync_signal_outcome_marks_stop_loss_as_loss():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            market_id="m1",
            market_name="Test",
            signal_type="BUY_NO",
            confidence=80,
            estimated_probability=0.3,
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
            status="SOLD",
            signal_id=sig.id,
            bayse_order_id="22222222-2222-2222-2222-222222222222",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        await sync_signal_outcome(session, trade, market_resolution="STOP_LOSS", payout=60.0)
        await session.commit()

        assert sig.resolution == "LOSS"
        assert sig.status == "LOST"
        assert sig.pnl == -40.0


@pytest.mark.asyncio
async def test_match_payout_activity_falls_back_to_event_and_market():
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
            bayse_order_id="",
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        payout_by_order, payout_by_event_market = index_payout_activities(
            [
                {
                    "type": "PAYOUT_WIN",
                    "eventId": "event-1",
                    "marketId": "m1",
                    "resolvedOutcome": "YES",
                    "payout": "180",
                    "createdAt": "2026-05-01T08:00:00Z",
                }
            ]
        )

        act, _sig = await match_payout_activity_for_trade(
            session,
            trade,
            payout_by_order,
            payout_by_event_market,
        )

        assert act is not None
        assert act["type"] == "PAYOUT_WIN"
