import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.signal import Signal
from app.models.trade import Trade
from app.models.feature_snapshot import FeatureSnapshot
from app.models.bayes_state import BayesState
from app.services.payout_reconciliation import index_payout_activities, match_payout_activity_for_trade
from app.services.outcome_sync import sync_signal_outcome, rebuild_bayes_state_from_resolved_trades
from app.services.bayes_state_keys import build_bayes_state_key_candidates, resolve_bayes_state_key
from app.services.storage import feature_snapshot_metrics, get_bayes_state, save_feature_snapshot
from app.services.feature_encoder import FeatureEncoding


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
        assert trade.resolved_at is not None


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
        assert trade.resolved_at is not None
        state = await get_bayes_state(session, state_key="default")
        assert state is not None
        assert state.yes_updates == 1
        assert state.no_updates == 0


@pytest.mark.asyncio
async def test_sync_signal_outcome_links_feature_snapshot_to_resolution():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    feature = FeatureEncoding(
        market_id="m-snap",
        market_name="Snapshot Test",
        semantic_vector=[],
        market_vector=[],
        portfolio_vector=[],
        cross_market_vector=[],
    )

    async with AsyncLocal() as session:
        snapshot = await save_feature_snapshot(
            session,
            market_id="m-snap",
            market_name="Snapshot Test",
            feature=feature,
            event_id="event-snap",
        )
        sig = Signal(
            event_id="event-snap",
            market_id="m-snap",
            market_name="Snapshot Test",
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
            market_id="m-snap",
            market_name="Snapshot Test",
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

        await sync_signal_outcome(session, trade, market_resolution="YES", payout=175.0)
        await session.commit()

        refreshed_snapshot = await session.get(FeatureSnapshot, snapshot.id)
        metrics = await feature_snapshot_metrics(session)

        assert refreshed_snapshot is not None
        assert refreshed_snapshot.resolved_signal_id == sig.id
        assert refreshed_snapshot.resolved_trade_id == trade.id
        assert metrics["total_snapshots"] == 1
        assert metrics["resolved_signal_snapshots"] == 1
        assert metrics["resolved_trade_snapshots"] == 1
        assert metrics["fully_linked_snapshots"] == 1
        assert metrics["unlinked_snapshots"] == 0


@pytest.mark.asyncio
async def test_sync_signal_outcome_updates_scoped_bayes_state():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig = Signal(
            market_id="m-scope",
            market_name="Scoped Test",
            signal_type="BUY_YES",
            confidence=75,
            estimated_probability=0.6,
            market_price_at_signal=50,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="EXECUTED",
            bayes_state_key="series:fx-gbpusd-1h",
        )
        session.add(sig)
        await session.commit()
        await session.refresh(sig)

        trade = Trade(
            market_id="m-scope",
            market_name="Scoped Test",
            side="BUY",
            shares=10,
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            signal_id=sig.id,
            bayse_order_id="44444444-4444-4444-4444-444444444444",
            bayes_state_key="series:fx-gbpusd-1h",
        )
        session.add(trade)
        await session.commit()

        await sync_signal_outcome(session, trade, market_resolution="YES", payout=160.0)
        await session.commit()

        scoped_state = await get_bayes_state(session, state_key="series:fx-gbpusd-1h")
        default_state = await get_bayes_state(session, state_key="default")

        assert scoped_state is not None
        assert scoped_state.yes_updates == 1
        assert scoped_state.no_updates == 0
        assert default_state is None or default_state.yes_updates == 0


@pytest.mark.asyncio
async def test_resolve_bayes_state_key_prefers_scoped_memory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add_all([
            BayesState(
                state_key="category:fx",
                model_version="v1",
                prior_json={"alpha": 3.0, "beta": 2.0, "prior_yes": 0.6},
                parameter_json={"alpha": 3.0, "beta": 2.0, "yes_updates": 2, "no_updates": 1},
                yes_updates=2,
                no_updates=1,
            ),
            BayesState(
                state_key="default",
                model_version="v1",
                prior_json={"alpha": 10.0, "beta": 12.0, "prior_yes": 0.4545},
                parameter_json={"alpha": 10.0, "beta": 12.0, "yes_updates": 5, "no_updates": 6},
                yes_updates=5,
                no_updates=6,
            ),
        ])
        await session.commit()

        key = await resolve_bayes_state_key(
            session,
            build_bayes_state_key_candidates(
                market_id="m1",
                category="fx",
                default_key="default",
            ),
            default_key="default",
        )

        assert key == "category:fx"


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


@pytest.mark.asyncio
async def test_rebuild_bayes_state_from_resolved_trades_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        sig_yes = Signal(
            event_id="event-yes",
            market_id="m1",
            market_name="Test Yes",
            signal_type="BUY_YES",
            confidence=80,
            estimated_probability=0.7,
            market_price_at_signal=50,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="WON",
            resolution="WIN",
        )
        sig_no = Signal(
            event_id="event-no",
            market_id="m2",
            market_name="Test No",
            signal_type="BUY_NO",
            confidence=60,
            estimated_probability=0.4,
            market_price_at_signal=50,
            expected_value=10,
            reasoning="ok",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="WON",
            resolution="WIN",
        )
        session.add_all([sig_yes, sig_no])
        await session.commit()
        await session.refresh(sig_yes)
        await session.refresh(sig_no)

        trade_yes = Trade(
            market_id="m1",
            market_name="Test Yes",
            side="BUY",
            shares=10,
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            resolution="WIN",
            signal_id=sig_yes.id,
            bayse_order_id="11111111-1111-1111-1111-111111111111",
        )
        trade_no = Trade(
            market_id="m2",
            market_name="Test No",
            side="BUY",
            shares=10,
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            resolution="WIN",
            signal_id=sig_no.id,
            bayse_order_id="22222222-2222-2222-2222-222222222222",
        )
        session.add_all([trade_yes, trade_no])
        await session.commit()

        summary = await rebuild_bayes_state_from_resolved_trades(session, state_key="default")
        state = await get_bayes_state(session, state_key="default")

        assert summary["scanned"] == 2
        assert summary["applied"] == 2
        assert summary["yes_updates"] == 1
        assert summary["no_updates"] == 1
        assert state is not None
        assert state.yes_updates == 1
        assert state.no_updates == 1

        summary2 = await rebuild_bayes_state_from_resolved_trades(session, state_key="default")
        state2 = await get_bayes_state(session, state_key="default")

        assert summary2["yes_updates"] == 1
        assert summary2["no_updates"] == 1
        assert state2 is not None
        assert state2.yes_updates == 1
        assert state2.no_updates == 1
