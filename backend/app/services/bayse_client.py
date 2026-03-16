import hashlib
import hmac
import base64
import json
import time
from typing import Any, Dict, Optional

import httpx
from cachetools import TTLCache

from app.config import settings
from app.utils.logger import logger


class BayseClient:
    """
    Thin async client for Bayse Markets REST API.
    Implements required headers and signing for write operations.
    Docs: https://docs.bayse.markets/llms.txt (index)
    """

    def __init__(self):
        self.base_url = settings.bayse_base_url.rstrip("/")
        self.public_key = settings.bayse_public_key
        self.secret_key = settings.bayse_secret_key
        self.default_currency = settings.bayse_default_currency
        self.mock_mode = settings.mock_mode
        self.client = httpx.AsyncClient(timeout=15.0)
        self.cache = TTLCache(maxsize=256, ttl=30)

    async def _request(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None, signed: bool = False):
        if self.mock_mode:
            return self._mock_response(path, method, params, json_body)

        url = f"{self.base_url}{path}"

        headers: Dict[str, str] = {}
        if signed or self.public_key:
            headers["X-Public-Key"] = self.public_key

        body_str = json.dumps(json_body) if json_body else ""
        if signed:
            timestamp = str(int(time.time()))
            body_hash = hashlib.sha256(body_str.encode()).hexdigest()
            payload = f"{timestamp}.{method.upper()}.{path}.{body_hash}"
            signature = base64.b64encode(
                hmac.new(self.secret_key.encode(), payload.encode(), hashlib.sha256).digest()
            ).decode()
            headers["X-Timestamp"] = timestamp
            headers["X-Signature"] = signature

        for attempt in range(3):
            try:
                resp = await self.client.request(method, url, params=params, content=body_str if body_str else None, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    await self._backoff(attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                logger.warning("Bayse request error %s %s attempt=%s err=%s", method, path, attempt, exc)
                if attempt == 2:
                    return self._fallback_response(path, method, params, json_body)
            except httpx.RequestError as exc:
                logger.warning("Bayse request error %s %s attempt=%s err=%s", method, path, attempt, exc)
                if attempt == 2:
                    return self._fallback_response(path, method, params, json_body)

    async def _backoff(self, attempt: int):
        delay = 0.5 * (2**attempt)
        import asyncio

        await asyncio.sleep(delay)

    #Public endpoints
    async def list_events(self, category: str | None = None, status: str | None = "open", keyword: str | None = None, page: int = 1, size: int = 20, trending: bool | None = None):
        cache_key = ("events", category, status, keyword, page, size, trending)
        if cache_key in self.cache:
            return self.cache[cache_key]
        params = {
            "category": category,
            "status": status,
            "keyword": keyword,
            "page": page,
            "size": size,
            "currency": self.default_currency,
        }
        if trending is not None:
            params["trending"] = trending
        data = await self._request("GET", "/pm/events", params=params)
        self.cache[cache_key] = data
        return data

    async def get_event(self, event_id: str):
        cache_key = ("event", event_id)
        if cache_key in self.cache:
            return self.cache[cache_key]
        data = await self._request("GET", f"/pm/events/{event_id}", params={"currency": self.default_currency})
        self.cache[cache_key] = data
        return data

    async def get_event_by_slug(self, slug: str):
        return await self._request("GET", f"/pm/events/slug/{slug}", params={"currency": self.default_currency})

    async def list_series(self, page: int = 1, size: int = 50):
        return await self._request("GET", "/pm/events/series", params={"page": page, "size": size})

    async def list_series_events(self, series_slug: str):
        return await self._request("GET", f"/pm/events/series/{series_slug}/lean-events")

    async def price_history(self, event_id: str, time_period: str = "24H", outcome: str | None = None, market_ids: list[str] | None = None):
        params: Dict[str, Any] = {"timePeriod": time_period}
        if outcome:
            params["outcome"] = outcome
        if market_ids:
            params["marketId[]"] = market_ids
        return await self._request("GET", f"/pm/events/{event_id}/price-history", params=params)

    async def order_book(self, outcome_ids: list[str], depth: int = 10):
        if not outcome_ids:
            return []
        params = {"outcomeId[]": outcome_ids, "depth": depth, "currency": self.default_currency}
        return await self._request("GET", "/pm/books", params=params)

    async def ticker(self, market_id: str, outcome: str | None = None, outcome_id: str | None = None):
        params: Dict[str, Any] = {}
        if outcome:
            params["outcome"] = outcome
        if outcome_id:
            params["outcomeId"] = outcome_id
        try:
            return await self._request("GET", f"/pm/markets/{market_id}/ticker", params=params)
        except httpx.HTTPStatusError:
            return {}
        except httpx.RequestError:
            return {}

    async def trades(self, market_id: str, limit: int = 50):
        return await self._request("GET", "/pm/trades", params={"marketId": market_id, "limit": limit})

    # ----- Authenticated reads -----
    async def get_portfolio(self):
        return await self._request("GET", "/pm/portfolio")

    async def list_orders(self, status: str | None = None, page: int = 1, size: int = 20):
        params = {"status": status, "page": page, "size": size}
        return await self._request("GET", "/pm/orders", params=params)

    async def get_order(self, order_id: str):
        return await self._request("GET", f"/pm/orders/{order_id}")

    async def get_activities(self, type: str | None = None, page: int = 1, size: int = 20):
        params = {"page": page, "size": size}
        if type:
            params["type"] = type
        return await self._request("GET", "/pm/activities", params=params)

    async def get_assets(self):
        return await self._request("GET", "/wallet/assets")

    # ----- Trading (signed) -----
    async def quote(self, event_id: str, market_id: str, side: str, outcome: str, amount: float, currency: str | None = None):
        body = {
            "side": side,
            "outcome": outcome,
            "amount": amount,
            "currency": currency or self.default_currency,
        }
        return await self._request("POST", f"/pm/events/{event_id}/markets/{market_id}/quote", json_body=body)

    async def place_order(self, event_id: str, market_id: str, side: str, outcome: str, amount: float, currency: str = "NGN", price: float | None = None):
        body = {
            "side": side,
            "outcome": outcome,
            "amount": amount,
            "currency": currency,
        }
        if price is not None:
            body["price"] = price
        return await self._request("POST", f"/pm/events/{event_id}/markets/{market_id}/orders", json_body=body, signed=True)

    async def cancel_order(self, order_id: str):
        return await self._request("DELETE", f"/pm/orders/{order_id}", signed=True)

    # ----- Utilities -----
    async def close(self):
        await self.client.aclose()

    def _mock_response(self, path: str, method: str, params: dict | None, body: dict | None):
        # Minimal mock payloads to keep frontend/dev running without live keys.
        if path.startswith("/pm/events") and method == "GET":
            if path.count("/") > 3:  # single event mock
                return {
                    "id": path.split("/")[-1],
                    "title": "Mock event",
                    "description": "Mock description",
                    "markets": [
                        {
                            "id": "mock-market",
                            "title": "Mock market?",
                            "outcome1Price": 0.55,
                            "outcome2Price": 0.45,
                        }
                    ],
                }
            return {
                "events": [
                    {
                        "id": "mock-event",
                        "title": "Will mock outcome happen?",
                        "category": "mock",
                        "markets": [
                            {
                                "id": "mock-market",
                                "title": "Mock market?",
                                "outcome1Price": 0.55,
                                "outcome2Price": 0.45,
                            }
                        ],
                    }
                ],
                "pagination": {"page": 1, "size": 1, "lastPage": 1, "totalCount": 1},
            }
        if path.startswith("/pm/portfolio"):
            return {"outcomeBalances": [], "portfolioCost": 0, "portfolioCurrentValue": 0, "portfolioPercentageChange": 0}
        if path.startswith("/pm/orders") and method == "GET":
            return {"orders": [], "pagination": {"page": 1, "size": 20, "lastPage": 1, "totalCount": 0}}
        if path.endswith("/quote"):
            return {
                "price": 0.5,
                "currentMarketPrice": 0.5,
                "quantity": (body or {}).get("amount", 100) * 2,
                "amount": (body or {}).get("amount", 100),
                "fee": 1.0,
                "profitPercentage": 50,
                "currencyBaseMultiplier": 1,
                "completeFill": True,
                "tradeGoesOverMaxLiability": False,
            }
        if path.endswith("/orders") and method == "POST":
            return {"engine": "AMM", "ammOrder": {"id": "mock", "status": "filled"}}
        return {"mock": True}

    def _fallback_response(self, path: str, method: str, params: dict | None, body: dict | None):
        if self.mock_mode:
            return self._mock_response(path, method, params, body)
        # graceful empty responses to avoid 500s in UI
        if path.startswith("/pm/events"):
            return {"events": [], "pagination": {"page": 1, "size": 0, "lastPage": 1, "totalCount": 0}}
        if path.startswith("/pm/portfolio"):
            return {"outcomeBalances": [], "portfolioCost": 0, "portfolioCurrentValue": 0, "portfolioPercentageChange": 0}
        if path.startswith("/pm/orders"):
            return {"orders": [], "pagination": {"page": 1, "size": 20, "lastPage": 1, "totalCount": 0}}
        return {"data_stale": True}


def get_bayse_client() -> BayseClient:
    return BayseClient()
