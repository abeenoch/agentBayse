import asyncio
import pytest

from app.services.ai_agent import AIAgent
from app.services.llm_client import LLMClient
from app.services.web_search import WebSearchService
from app.services.bayse_client import BayseClient


class DummyLLM(LLMClient):
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        return '{"market_id":"m1","market_name":"Test","signal":"BUY_YES","confidence":80,"estimated_probability":0.7,"current_market_price":55,"expected_value":8.5,"reasoning":"looks good","sources":[],"suggested_stake":100,"risk_level":"LOW"}'


class DummySearch(WebSearchService):
    async def search(self, query: str, max_results: int = 5):
        return {"results": []}


class DummyBayse(BayseClient):
    async def get_event(self, event_id: str):
        return {
            "id": event_id,
            "title": "Mock event",
            "markets": [
                {
                    "id": "m1",
                    "title": "Mock market?",
                    "outcome1Price": 0.55,
                    "outcome2Price": 0.45,
                }
            ],
        }

    async def get_portfolio(self):
        return {"portfolioCurrentValue": 10_000}


@pytest.mark.asyncio
async def test_agent_parses_llm_json(monkeypatch):
    agent = AIAgent(llm=DummyLLM(), search_service=DummySearch(), bayse_client=DummyBayse())
    signal = await agent.analyze_market("evt")
    assert signal is not None
    assert signal.signal == "BUY_YES"
    assert signal.confidence == 80
