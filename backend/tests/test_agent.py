import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.agent_config import AgentConfig
from app.models.bayes_training_run import BayesTrainingRun
from app.models.feature_snapshot import FeatureSnapshot
from app.models.bayes_state import BayesState
from app.models.event_market import EventMarket
from app.models.signal import Signal
from app.models.trade import Trade
from app.routers.agent import bayes_audit, bayes_report, trade_pipeline_trace
from app.services.ai_agent import AIAgent, SignalOutput
from app.services.web_search import WebSearchService
from app.services.bayse_client import BayseClient
from app.services.llm_client import call_llm


GOOD_SIGNAL_JSON = """{
    "market_id": "m1",
    "market_name": "Mock market?",
    "signal": "BUY_YES",
    "confidence": 80,
    "estimated_probability": 0.7,
    "current_market_price": 0.55,
    "expected_value": 8.5,
    "reasoning": "looks good",
    "sources": [],
    "suggested_stake": 100.0,
    "risk_level": "LOW"
}"""


@pytest.fixture(autouse=True)
def disable_rag(monkeypatch):
    async def fake_ingest_market(*args, **kwargs):
        return 0

    monkeypatch.setattr("app.services.rag.ingest_market", fake_ingest_market)
    monkeypatch.setattr("app.services.rag.query", lambda *args, **kwargs: [])


class DummySearch(WebSearchService):
    async def search(self, query: str, **kwargs):
        return {"results": [{"url": "https://example.com", "snippet": "test news"}]}


class DummyBayse(BayseClient):
    async def get_event(self, event_id: str):
        return {
            "id": event_id,
            "title": "Mock event",
            "markets": [{"id": "m1", "title": "Mock market?", "outcome1Price": 0.55, "outcome2Price": 0.45}],
        }

    async def get_portfolio(self):
        return {"portfolioCurrentValue": 10_000}


@pytest.mark.asyncio
async def test_agent_returns_valid_signal(monkeypatch):
    async def fake_llm(prompt, system=""):
        return GOOD_SIGNAL_JSON

    monkeypatch.setattr("app.services.ai_agent.call_llm", fake_llm)
    agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
    signal = await agent.analyze_market("m1")
    assert signal is not None
    assert signal.signal == "BUY_YES"
    assert signal.confidence >= 80
    assert signal.risk_level == "LOW"


@pytest.mark.asyncio
async def test_analyze_market_does_not_finalize_event_market(monkeypatch):
    async def fake_llm(prompt, system=""):
        return GOOD_SIGNAL_JSON

    monkeypatch.setattr("app.services.ai_agent.call_llm", fake_llm)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add(EventMarket(event_id="event-1", market_id="m1", status="PENDING"))
        await session.commit()

        agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
        signal = await agent.analyze_market(
            "m1",
            event={
                "id": "event-1",
                "title": "Mock event",
                "description": "Mock description",
                "markets": [{"id": "m1", "title": "Mock market?", "outcome1Price": 0.55, "outcome2Price": 0.45}],
            },
            session=session,
        )
        assert signal is not None

        em = await session.get(EventMarket, {"event_id": "event-1", "market_id": "m1"})
        assert em is not None
        assert em.status == "PENDING"
        assert em.last_analyzed_at is not None


@pytest.mark.asyncio
async def test_agent_handles_yes_title_without_crashing(monkeypatch):
    async def fake_llm(prompt, system=""):
        return GOOD_SIGNAL_JSON

    monkeypatch.setattr("app.services.ai_agent.call_llm", fake_llm)
    agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
    event = {
        "id": "event-1",
        "title": "Yes",
        "description": "Should fall back to the event description when the title is generic.",
        "markets": [{"id": "m1", "title": "Yes", "outcome1Price": 0.55, "outcome2Price": 0.45}],
    }
    signal = await agent.analyze_market("m1", event=event)
    assert signal is not None
    assert signal.market_name == "Should fall back to the event description when the title is generic."


@pytest.mark.asyncio
async def test_agent_normalizes_negative_ev_input(monkeypatch):

    async def fake_llm(prompt, system=""):
        import json
        data = {
            "market_id": "m1", "market_name": "Mock market?",
            "signal": "BUY_YES", "confidence": 80,
            "estimated_probability": 0.3,  # below price → negative EV
            "current_market_price": 0.55, "expected_value": -5.0,
            "reasoning": "bad", "sources": [], "suggested_stake": 100.0, "risk_level": "LOW",
        }
        return json.dumps(data)

    monkeypatch.setattr("app.services.ai_agent.call_llm", fake_llm)
    agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
    signal = await agent.analyze_market("m1")
    assert signal is not None
    assert signal.expected_value >= 0


def test_normalized_stake_does_not_exceed_deployable_cap():
    agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
    stake = agent._normalized_stake(
        raw_stake=0.0,
        balance=80.0,
        available_to_deploy=80.0,
        open_positions=0,
        confidence=50,
        cfg=None,
    )
    assert stake <= 80.0
    assert stake == pytest.approx(26.67, rel=1e-3)


@pytest.mark.asyncio
async def test_fallback_search_called(monkeypatch):
    captured = {}

    class CapturingSearch(WebSearchService):
        async def search(self, query: str, **kwargs):
            captured["query"] = query
            return {"results": [{"url": "https://news.com", "snippet": "big news"}]}

    async def fake_llm(prompt, system=""):
        return GOOD_SIGNAL_JSON

    monkeypatch.setattr("app.services.ai_agent.call_llm", fake_llm)
    agent = AIAgent(search_service=CapturingSearch(), bayse_client=DummyBayse())
    await agent.analyze_market("m1")
    assert "query" in captured


@pytest.mark.asyncio
async def test_bayes_report_and_trace_follow_scoped_state(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add(AgentConfig(bayes_state_key="series:fx-gbpusd-1h"))
        session.add_all([
            BayesState(
                state_key="default",
                model_version="v1",
                prior_json={"alpha": 2.0, "beta": 2.0, "prior_yes": 0.5},
                parameter_json={"alpha": 2.0, "beta": 2.0, "yes_updates": 0, "no_updates": 0},
                yes_updates=0,
                no_updates=0,
            ),
            BayesState(
                state_key="series:fx-gbpusd-1h",
                model_version="v1",
                prior_json={"alpha": 5.0, "beta": 3.0, "prior_yes": 0.625},
                parameter_json={"alpha": 5.0, "beta": 3.0, "yes_updates": 4, "no_updates": 2},
                yes_updates=4,
                no_updates=2,
            ),
        ])
        signal = Signal(
            market_id="m1",
            market_name="Scoped Market",
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
            bayes_state_key="series:fx-gbpusd-1h",
        )
        session.add(signal)
        await session.commit()
        await session.refresh(signal)

        trade = Trade(
            market_id="m1",
            market_name="Scoped Market",
            side="BUY",
            shares=10,
            price=0.5,
            total_cost=100.0,
            status="EXECUTED",
            resolution="WIN",
            signal_id=signal.id,
            bayse_order_id="33333333-3333-3333-3333-333333333333",
            bayes_state_key="series:fx-gbpusd-1h",
        )
        session.add(trade)
        await session.commit()

        report = await bayes_report(session=session)
        trace = await trade_pipeline_trace(market_id="m1", session=session, client=None)

        assert report["bayes_state"]["state_key"] == "series:fx-gbpusd-1h"
        assert report["bayes_state"]["yes_updates"] == 4
        assert trace["bayes_state"]["state_key"] == "series:fx-gbpusd-1h"
        assert trace["resolved_bayes_state_key"] == "series:fx-gbpusd-1h"


@pytest.mark.asyncio
async def test_bayes_audit_separates_yes_and_no(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add(AgentConfig(bayes_state_key="default"))
        yes_signal = Signal(
            market_id="m1",
            market_name="Yes market",
            signal_type="BUY_YES",
            confidence=82,
            estimated_probability=0.72,
            market_price_at_signal=0.55,
            expected_value=9.0,
            reasoning="yes edge",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="EXECUTED",
            bayes_state_key="default",
        )
        no_signal = Signal(
            market_id="m2",
            market_name="No market",
            signal_type="BUY_NO",
            confidence=74,
            estimated_probability=0.69,
            market_price_at_signal=0.61,
            expected_value=7.0,
            reasoning="no edge",
            sources=[],
            suggested_stake=100,
            risk_level="LOW",
            status="EXECUTED",
            bayes_state_key="default",
        )
        session.add_all([yes_signal, no_signal])
        await session.commit()
        await session.refresh(yes_signal)
        await session.refresh(no_signal)

        session.add_all([
            Trade(
                market_id="m1",
                market_name="Yes market",
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
            ),
            Trade(
                market_id="m2",
                market_name="No market",
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
            ),
        ])
        await session.commit()

        audit = await bayes_audit(session=session)

        assert audit["total_signals"] == 2
        assert audit["yes"]["count"] == 1
        assert audit["no"]["count"] == 1
        assert audit["yes"]["wins"] == 1
        assert audit["no"]["wins"] == 1
        assert audit["resolved_trades"] == 2
        assert audit["side_bias"] == "BALANCED"


@pytest.mark.asyncio
async def test_live_bayes_policy_uses_training_weights_and_records_model_version():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    AsyncLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncLocal() as session:
        session.add(
            AgentConfig(
                auto_trade=False,
                max_open_positions=3,
                balance_floor=0.0,
                min_confidence=65,
                balance_reserve_pct=0.30,
                bayes_live_decision_mode=True,
                bayes_state_key="series:fx-gbpusd-1h",
            )
        )
        session.add(
            BayesTrainingRun(
                state_key="default",
                model_version="logreg_v1",
                sample_size=10,
                train_size=8,
                test_size=2,
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
                    "weights": [4.0] + [0.0] * 11,
                    "means": [0.0] * 13,
                    "stds": [1.0] * 13,
                },
                metrics_json={},
                calibration_json={},
            )
        )
        await session.commit()

        agent = AIAgent(search_service=DummySearch(), bayse_client=DummyBayse())
        signal = await agent.analyze_market("m1", session=session)

        assert signal is not None
        assert signal.signal == "BUY_NO"
        assert signal.reasoning.startswith("Trained Bayes policy")

        snapshot_result = await session.execute(
            select(FeatureSnapshot).where(FeatureSnapshot.market_id == "m1").order_by(FeatureSnapshot.created_at.desc()).limit(1)
        )
        snapshot = snapshot_result.scalars().first()
        assert snapshot is not None
        assert snapshot.posterior_action == "BUY_NO"
        assert snapshot.model_version == "logreg_v1"
