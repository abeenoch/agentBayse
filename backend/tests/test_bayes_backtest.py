from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.market_snapshot import MarketSnapshot
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayes_backtest import (
    get_cached_backtest_snapshot,
    load_backtest_rows,
    refresh_backtest_snapshots,
    score_policy,
    sweep_policies,
)


@pytest.mark.asyncio
async def test_bayes_backtest_prefers_more_selective_policy():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.utcnow()
    async with AsyncLocal() as session:
        session.add(
            MarketSnapshot(
                market_id="m1",
                event_id="e1",
                title="Low edge loser",
                outcome1_label="YES",
                outcome1_price=0.58,
                outcome2_label="NO",
                outcome2_price=0.42,
                liquidity=2400.0,
                total_volume=12000.0,
                total_orders=64,
                raw={},
                created_at=now - timedelta(minutes=6),
            )
        )
        session.add_all([
            Signal(
                market_id="m1",
                market_name="Low edge loser",
                signal_type="BUY_YES",
                confidence=72,
                estimated_probability=0.56,
                market_price_at_signal=0.52,
                expected_value=2.0,
                reasoning="low edge",
                sources=[],
                suggested_stake=100,
                risk_level="LOW",
                status="LOST",
                resolution="LOSS",
                pnl=-10.0,
                bayes_state_key="default",
                created_at=now - timedelta(minutes=4),
            ),
            Signal(
                market_id="m2",
                market_name="High conviction winner",
                signal_type="BUY_YES",
                confidence=72,
                estimated_probability=0.67,
                market_price_at_signal=0.55,
                expected_value=7.0,
                reasoning="good edge",
                sources=[],
                suggested_stake=100,
                risk_level="MEDIUM",
                status="WON",
                resolution="WIN",
                pnl=12.0,
                bayes_state_key="default",
                created_at=now - timedelta(minutes=3),
            ),
            Signal(
                market_id="m3",
                market_name="Another winner",
                signal_type="BUY_NO",
                confidence=80,
                estimated_probability=0.71,
                market_price_at_signal=0.60,
                expected_value=8.0,
                reasoning="strong no",
                sources=[],
                suggested_stake=100,
                risk_level="MEDIUM",
                status="WON",
                resolution="WIN",
                pnl=15.0,
                bayes_state_key="default",
                created_at=now - timedelta(minutes=2),
            ),
            Signal(
                market_id="m4",
                market_name="Marginal loser",
                signal_type="BUY_YES",
                confidence=66,
                estimated_probability=0.58,
                market_price_at_signal=0.53,
                expected_value=5.5,
                reasoning="thin edge",
                sources=[],
                suggested_stake=100,
                risk_level="LOW",
                status="LOST",
                resolution="LOSS",
                pnl=-8.0,
                bayes_state_key="default",
                created_at=now - timedelta(minutes=1),
            ),
        ])
        await session.commit()

        signals = (await session.execute(select(Signal).order_by(Signal.created_at.asc()))).scalars().all()
        session.add_all([
            Trade(
                market_id=signals[0].market_id,
                market_name=signals[0].market_name,
                side="BUY",
                shares=10,
                price=0.52,
                total_cost=52.0,
                status="EXECUTED",
                resolution="LOSS",
                pnl=-10.0,
                signal_id=signals[0].id,
                bayse_order_id="11111111-1111-1111-1111-111111111111",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=4),
            ),
            Trade(
                market_id=signals[1].market_id,
                market_name=signals[1].market_name,
                side="BUY",
                shares=10,
                price=0.55,
                total_cost=55.0,
                status="EXECUTED",
                resolution="WIN",
                pnl=12.0,
                signal_id=signals[1].id,
                bayse_order_id="22222222-2222-2222-2222-222222222222",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=3),
            ),
            Trade(
                market_id=signals[2].market_id,
                market_name=signals[2].market_name,
                side="BUY",
                shares=10,
                price=0.60,
                total_cost=60.0,
                status="EXECUTED",
                resolution="WIN",
                pnl=15.0,
                signal_id=signals[2].id,
                bayse_order_id="33333333-3333-3333-3333-333333333333",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=2),
            ),
            Trade(
                market_id=signals[3].market_id,
                market_name=signals[3].market_name,
                side="BUY",
                shares=10,
                price=0.53,
                total_cost=53.0,
                status="EXECUTED",
                resolution="LOSS",
                pnl=-8.0,
                signal_id=signals[3].id,
                bayse_order_id="44444444-4444-4444-4444-444444444444",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=1),
            ),
        ])
        await session.commit()

        rows = await load_backtest_rows(session, state_key="default")
        baseline = score_policy(rows, min_confidence=60, min_expected_value=0.0)
        strict = score_policy(rows, min_confidence=70, min_expected_value=6.0)
        sweep = sweep_policies(
            rows,
            confidence_thresholds=[60, 70],
            expected_value_thresholds=[0.0, 6.0],
        )
        cached = await refresh_backtest_snapshots(session, state_key="default")
        cached_all_time = await get_cached_backtest_snapshot(
            session,
            state_key="default",
            period_kind="all_time",
            period_key="all_time",
        )

        assert len(rows) == 4
        assert rows[0].market_liquidity and rows[0].market_liquidity > 0
        assert rows[0].market_spread and rows[0].market_spread > 0
        assert baseline["trades_taken"] == 4
        assert baseline["win_rate"] == pytest.approx(0.5)
        assert strict["trades_taken"] == 2
        assert strict["win_rate"] == pytest.approx(1.0)
        assert strict["total_pnl"] > baseline["total_pnl"]
        assert strict["max_drawdown"] <= baseline["max_drawdown"]
        assert sweep["best_by_pnl"]["min_confidence"] == 70
        assert sweep["best_by_pnl"]["min_expected_value"] == 6.0
        assert cached["rows_scored"] == 4
        assert cached_all_time is not None
        assert cached_all_time.rows_scored == 4
