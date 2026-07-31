from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayes_training import build_offline_eval_report


@pytest.mark.asyncio
async def test_offline_eval_builds_walk_forward_report():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.utcnow()
    async with AsyncLocal() as session:
        for idx in range(6):
            signal_type = "BUY_YES" if idx % 2 == 0 else "BUY_NO"
            signal = Signal(
                market_id=f"m{idx}",
                market_name=f"Market {idx}",
                signal_type=signal_type,
                confidence=70 + idx,
                estimated_probability=0.6 + idx * 0.01,
                market_price_at_signal=0.5,
                expected_value=5.0 + idx,
                reasoning="test",
                sources=[],
                suggested_stake=100,
                risk_level="LOW",
                status="EXECUTED",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=6 - idx),
            )
            session.add(signal)
            await session.commit()
            await session.refresh(signal)
            trade = Trade(
                market_id=signal.market_id,
                market_name=signal.market_name,
                side="BUY",
                shares=10,
                price=0.5,
                total_cost=50.0,
                status="EXECUTED",
                resolution="WIN" if idx % 3 != 0 else "LOSS",
                pnl=10.0 if idx % 3 != 0 else -5.0,
                signal_id=signal.id,
                bayse_order_id=f"{idx:08d}-{idx:04d}-{idx:04d}-{idx:04d}-{idx:012d}",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=6 - idx),
            )
            session.add(trade)
            await session.commit()

        report = await build_offline_eval_report(
            session,
            state_key="default",
            min_train_size=4,
            test_size=2,
            step_size=2,
        )

        assert report["sample_size"] == 6
        assert report["fold_count"] == 1
        assert report["overall_metrics"]["accuracy"] >= 0.0
        assert report["model_policy"]["trades_taken"] >= 0
        assert report["baseline_policy"]["trades_taken"] >= 0
