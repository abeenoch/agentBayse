import pytest
import asyncio

from app.services.bayse_client import BayseClient


@pytest.mark.asyncio
async def test_order_book_empty_outcomes():
    client = BayseClient()
    result = await client.order_book([])
    assert result == []


@pytest.mark.asyncio
async def test_price_history_passes_market_ids(monkeypatch):
    client = BayseClient()
    captured = {}

    async def fake_request(method, path, params=None, json_body=None, signed=False):
        captured["params"] = params
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)
    await client.price_history("evt", time_period="1W", outcome="YES", market_ids=["m1", "m2"])
    assert captured["params"]["marketId[]"] == ["m1", "m2"]
    assert captured["params"]["outcome"] == "YES"
