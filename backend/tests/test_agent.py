import pytest

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
async def test_agent_skips_negative_ev(monkeypatch):
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
    # risk_guard blocks negative EV
    assert signal is None


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
