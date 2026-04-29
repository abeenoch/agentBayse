import pytest
from types import SimpleNamespace
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.trade import Trade
from app.services.risk_guard import check_trade_limits


def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, AsyncLocal


@pytest.mark.asyncio
async def test_trade_limits_block_at_cap():
    engine, AsyncLocal = _make_session()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cfg = SimpleNamespace(max_open_positions=2)

    async with AsyncLocal() as session:
        session.add_all([
            Trade(market_id="m1", market_name="t1", side="BUY", shares=1, price=1.0, total_cost=10, status="EXECUTED"),
            Trade(market_id="m2", market_name="t2", side="BUY", shares=1, price=1.0, total_cost=10, status="EXECUTED"),
        ])
        await session.commit()

        result = await check_trade_limits(session, cfg)
        assert not result.passed
        assert any("cap" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_trade_limits_allow_below_cap():
    engine, AsyncLocal = _make_session()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cfg = SimpleNamespace(max_open_positions=3)

    async with AsyncLocal() as session:
        session.add(Trade(market_id="m1", market_name="t1", side="BUY", shares=1, price=1.0, total_cost=10, status="EXECUTED"))
        await session.commit()

        result = await check_trade_limits(session, cfg)
        assert result.passed
