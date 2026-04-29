import pytest
import httpx

from app.services.bayse_client import BayseAuthError, BayseClient


@pytest.mark.asyncio
async def test_client_instantiates():
    client = BayseClient()
    assert client.base_url.endswith("/v1")


@pytest.mark.asyncio
async def test_signed_401_raises(monkeypatch):
    client = BayseClient()
    client.mock_mode = False
    client.public_key = "pk_test"
    client.secret_key = "sk_test"

    class DummyResponse:
        status_code = 401
        text = '{"error":"invalid_signature","message":"The provided signature does not match the expected signature"}'

        def json(self):
            return {"error": "invalid_signature", "message": "The provided signature does not match the expected signature"}

        def raise_for_status(self):
            request = httpx.Request("POST", "https://relay.bayse.markets/v1/pm/orders")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401", request=request, response=response)

    async def fake_request(*args, **kwargs):
        return DummyResponse()

    monkeypatch.setattr(client.client, "request", fake_request)

    with pytest.raises(BayseAuthError):
        await client.place_order(
            event_id="evt",
            market_id="mkt",
            side="BUY",
            amount=100,
            outcome="YES",
        )


@pytest.mark.asyncio
async def test_place_order_uses_documented_outcome(monkeypatch):
    client = BayseClient()
    client.mock_mode = False
    client.public_key = "pk_test"
    client.secret_key = "sk_test"

    captured = {}

    class DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    async def fake_request(method, url, params=None, content=None, headers=None):
        captured["method"] = method
        captured["url"] = url
        captured["content"] = content
        return DummyResponse()

    monkeypatch.setattr(client.client, "request", fake_request)

    await client.place_order(
        event_id="evt",
        market_id="mkt",
        side="BUY",
        amount=100,
        outcome_id="oid-yes",
    )

    assert captured["method"] == "POST"
    assert b'"outcome":"YES"' in captured["content"]
    assert b'"outcomeId":"oid-yes"' in captured["content"]
