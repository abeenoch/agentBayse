import pytest
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.scheduler import reconcile_open_trades, collect_live_trade_diagnostics, normalize_terminal_trades


class FakeBayseClient:
    def __init__(self):
        self.calls: list[int] = []

    async def get_activities(self, type: str, page: int = 1, size: int = 50):
        page = len(self.calls) + 1
        self.calls.append(page)
        if page == 1:
            return {
                "activities": [
                    {
                        "type": "PAYOUT_LOSS",
                        "orderId": "44444444-4444-4444-4444-444444444444",
                        "eventId": "event-x",
                        "marketId": "mx",
                        "resolvedOutcome": "NO",
                        "payout": "0",
                        "createdAt": "2026-05-01T10:00:00Z",
                    }
                ]
            }
        if page == 2:
            return {
                "activities": [
                    {
                        "type": "PAYOUT_WIN",
                        "orderId": "33333333-3333-3333-3333-333333333333",
                        "eventId": "event-1",
                        "marketId": "m1",
                        "resolvedOutcome": "YES",
                        "payout": "180",
                        "createdAt": "2026-05-01T08:00:00Z",
                    }
                ]
            }
        if page == 3:
            return {
                "activities": [
                    {
                        "type": "PAYOUT_WIN",
                        "orderId": "55555555-5555-5555-5555-555555555555",
                        "eventId": "event-old",
                        "marketId": "mo",
                        "resolvedOutcome": "YES",
                        "payout": "180",
                        "createdAt": "2026-05-01T06:00:00Z",
                    }
                ]
            }
        return {"activities": []}

    async def get_order(self, order_id: str):
        return {"status": "filled"}


@pytest.mark.asyncio
async def test_collect_live_trade_diagnostics_reports_match_and_age():
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
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = FakeBayseClient()
        diagnostics = await collect_live_trade_diagnostics(session, client)

        assert len(diagnostics) == 1
        row = diagnostics[0]
        assert row["market_id"] == "m1"
        assert row["age"]["seconds"] is not None
        assert row["bayse_match"]["matched"] is True
        assert row["bayse_match"]["match_type"] == "order_id"


@pytest.mark.asyncio
async def test_reconcile_open_trades_resolves_payout_on_boot():
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
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = FakeBayseClient()
        checked, resolved = await reconcile_open_trades(session, client)

        await session.refresh(sig)
        await session.refresh(trade)

        assert checked == 1
        assert resolved == 1
        assert client.calls == [1, 2, 3]
        assert sig.resolution == "WIN"
        assert sig.status == "WON"
        assert trade.resolution == "WIN"
        assert trade.pnl == 80.0
        assert trade.resolved_at is not None


@pytest.mark.asyncio
async def test_reconcile_open_trades_rescues_stale_expired_trade():
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
            status="STALE",
            resolution="EXPIRED",
            signal_id=sig.id,
            bayse_order_id="33333333-3333-3333-3333-333333333333",
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = FakeBayseClient()
        checked, resolved = await reconcile_open_trades(session, client)

        await session.refresh(sig)
        await session.refresh(trade)

        assert checked == 1
        assert resolved == 1
        assert trade.status == "EXECUTED"
        assert trade.resolution == "WIN"
        assert trade.resolved_at is not None
        assert sig.resolution == "WIN"
        assert sig.status == "WON"


@pytest.mark.asyncio
async def test_reconcile_open_trades_handles_executed_expired_trade():
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
            resolution="EXPIRED",
            signal_id=sig.id,
            bayse_order_id="33333333-3333-3333-3333-333333333333",
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = FakeBayseClient()
        checked, resolved = await reconcile_open_trades(session, client)

        await session.refresh(sig)
        await session.refresh(trade)

        assert checked == 1
        assert resolved == 1
        assert trade.status == "EXECUTED"
        assert trade.resolution == "WIN"
        assert trade.resolved_at is not None
        assert sig.resolution == "WIN"
        assert sig.status == "WON"


@pytest.mark.asyncio
async def test_normalize_terminal_trades_moves_executed_expired_to_stale():
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

        normalized = await normalize_terminal_trades(session)
        await session.commit()
        await session.refresh(trade)

        assert normalized == 1
        assert trade.status == "STALE"
        assert trade.resolution == "EXPIRED"


@pytest.mark.asyncio
async def test_reconcile_open_trades_handles_space_separated_payout_type():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class SpaceTypeClient(FakeBayseClient):
        async def get_activities(self, type: str, page: int = 1, size: int = 50):
            page = len(self.calls) + 1
            self.calls.append(page)
            if page == 1:
                return {
                    "activities": [
                        {
                            "type": "PAYOUT WIN",
                            "orderId": "33333333-3333-3333-3333-333333333333",
                            "eventId": "event-1",
                            "marketId": "m1",
                            "resolvedOutcome": "YES",
                            "payout": "180",
                            "createdAt": "2026-05-01T08:00:00Z",
                        }
                    ]
                }
            return {"activities": []}

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
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = SpaceTypeClient()
        checked, resolved = await reconcile_open_trades(session, client)

        await session.refresh(sig)
        await session.refresh(trade)

        assert checked == 1
        assert resolved == 1
        assert trade.resolution == "WIN"
        assert trade.resolved_at is not None
        assert sig.resolution == "WIN"
        assert sig.status == "WON"


@pytest.mark.asyncio
async def test_reconcile_open_trades_handles_nested_activity_payload():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class NestedPayloadClient(FakeBayseClient):
        async def get_activities(self, type: str, page: int = 1, size: int = 50):
            page = len(self.calls) + 1
            self.calls.append(page)
            if page == 1:
                return {
                    "data": {
                        "activities": [
                            {
                                "type": "PAYOUT_WIN",
                                "orderId": "33333333-3333-3333-3333-333333333333",
                                "eventId": "event-1",
                                "marketId": "m1",
                                "resolvedOutcome": "YES",
                                "payout": "180",
                                "createdAt": "2026-05-01T08:00:00Z",
                            }
                        ]
                    }
                }
            return {"data": {"activities": []}}

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
            created_at=datetime(2026, 5, 1, 7, 30, 0),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        client = NestedPayloadClient()
        checked, resolved = await reconcile_open_trades(session, client)

        await session.refresh(sig)
        await session.refresh(trade)

        assert checked == 1
        assert resolved == 1
        assert trade.resolution == "WIN"
        assert sig.resolution == "WIN"
