import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import portfolio_snapshot  # noqa: F401
from app.services.storage import save_market_snapshot, save_portfolio_snapshot


@pytest.mark.asyncio
async def test_save_market_snapshot_persists_raw_order_book():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        saved = await save_market_snapshot(
            session,
            market_id="m1",
            event_id="e1",
            title="Market 1",
            market={
                "outcome1Price": 0.55,
                "outcome2Price": 0.45,
                "liquidity": 1200,
                "totalVolume": 8000,
                "totalOrders": 14,
                "order_book": {
                    "bids": [{"price": 0.54, "total": 100}],
                    "asks": [{"price": 0.56, "total": 90}],
                },
            },
        )

        assert saved.raw["order_book"]["bids"][0]["price"] == 0.54
        assert saved.raw["order_book"]["asks"][0]["total"] == 90


@pytest.mark.asyncio
async def test_save_portfolio_snapshot_persists_snapshot_data():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        saved = await save_portfolio_snapshot(
            session,
            total_balance=25000.0,
            invested=5000.0,
            unrealized_pnl=320.5,
            realized_pnl_today=45.0,
            positions_count=3,
            snapshot_data={
                "market_id": "m1",
                "market_name": "Market 1",
                "wallet_balance": 25000.0,
                "available_to_deploy": 14000.0,
            },
        )

        assert saved.total_balance == 25000.0
        assert saved.positions_count == 3
        assert saved.snapshot_data["market_id"] == "m1"
        assert saved.snapshot_data["available_to_deploy"] == 14000.0
