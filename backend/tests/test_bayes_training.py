from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.bayes_training_run import BayesTrainingRun
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayes_training import (
    _apply_standardize,
    _standardize,
    build_calibration_audit,
    get_latest_training_run,
    resolve_live_training_run,
    train_bayes_model,
)
from app.services.bayes_backtest import _is_win_for_signal


def test_standardization_preserves_bias_feature():
    matrix = [
        [1.0, 10.0, 2.0],
        [1.0, 12.0, 4.0],
        [1.0, 14.0, 6.0],
    ]

    standardized, means, stds = _standardize(matrix)
    reapplied = _apply_standardize([[1.0, 16.0, 8.0]], means, stds)

    assert all(row[0] == 1.0 for row in standardized)
    assert reapplied[0][0] == 1.0


def test_buy_no_label_mapping_treats_trade_loss_as_signal_win():
    assert _is_win_for_signal("BUY_YES", "WIN") is True
    assert _is_win_for_signal("BUY_YES", "LOSS") is False
    assert _is_win_for_signal("BUY_NO", "LOSS") is True
    assert _is_win_for_signal("BUY_NO", "WIN") is False


@pytest.mark.asyncio
async def test_bayes_training_and_calibration_pipeline():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.utcnow()
    async with AsyncLocal() as session:
        session.add_all([
            Signal(
                market_id="m1",
                market_name="Yes winner",
                signal_type="BUY_YES",
                confidence=80,
                estimated_probability=0.72,
                market_price_at_signal=0.55,
                expected_value=8.0,
                reasoning="yes",
                sources=[],
                suggested_stake=100,
                risk_level="LOW",
                status="EXECUTED",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=2),
            ),
            Signal(
                market_id="m2",
                market_name="No winner",
                signal_type="BUY_NO",
                confidence=74,
                estimated_probability=0.68,
                market_price_at_signal=0.61,
                expected_value=6.0,
                reasoning="no",
                sources=[],
                suggested_stake=100,
                risk_level="LOW",
                status="EXECUTED",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=1),
            ),
        ])
        await session.commit()
        signals = (await session.execute(select(Signal).order_by(Signal.created_at.asc()))).scalars().all()
        yes_signal = signals[0]
        no_signal = signals[1]
        session.add_all([
            Trade(
                market_id=yes_signal.market_id,
                market_name=yes_signal.market_name,
                side="BUY",
                shares=10,
                price=0.55,
                total_cost=55.0,
                status="EXECUTED",
                resolution="WIN",
                pnl=12.0,
                signal_id=yes_signal.id,
                bayse_order_id="11111111-1111-1111-1111-111111111111",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=2),
            ),
            Trade(
                market_id=no_signal.market_id,
                market_name=no_signal.market_name,
                side="BUY",
                shares=10,
                price=0.61,
                total_cost=61.0,
                status="EXECUTED",
                resolution="LOSS",
                pnl=15.0,
                signal_id=no_signal.id,
                bayse_order_id="22222222-2222-2222-2222-222222222222",
                bayes_state_key="default",
                created_at=now - timedelta(minutes=1),
            ),
        ])
        await session.commit()

        calibration = await build_calibration_audit(session, state_key="default")
        training = await train_bayes_model(session, state_key="default")
        latest = await get_latest_training_run(session, state_key="default")

        assert calibration["sample_size"] == 2
        assert calibration["yes"]["count"] == 1
        assert calibration["no"]["count"] == 1
        assert training["sample_size"] == 2
        assert training["train_size"] == 1
        assert training["test_size"] == 1
        assert latest is not None
        assert latest.state_key == "default"


@pytest.mark.asyncio
async def test_live_training_run_falls_back_to_default_state():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add(
            BayesTrainingRun(
                state_key="default",
                model_version="logreg_v1",
                sample_size=2,
                train_size=1,
                test_size=1,
                positive_rate=0.5,
                feature_names=[
                    "bias",
                    "is_buy_no",
                    "confidence",
                    "estimated_probability",
                    "market_price",
                    "expected_value",
                    "rank_score",
                    "market_liquidity",
                    "market_volume",
                    "market_orders",
                    "market_spread",
                    "market_imbalance",
                    "snapshot_age_seconds",
                ],
                coefficients={
                    "bias": 0.0,
                    "weights": [1.5] + [0.0] * 11,
                    "means": [0.0] * 13,
                    "stds": [1.0] * 13,
                },
                metrics_json={},
                calibration_json={},
                trained_at=datetime.utcnow(),
            )
        )
        await session.commit()

        run, resolved_key = await resolve_live_training_run(
            session,
            state_key="series:fx-gbpusd-1h",
            default_key="default",
        )

        assert run is not None
        assert run.state_key == "default"
        assert resolved_key == "default"
