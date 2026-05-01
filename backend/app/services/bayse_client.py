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


class BayseAuthError(RuntimeError):
    pass


class BayseRequestError(RuntimeError):
    pass


class BayseClient:
    """
    Thin async client for Bayse Markets REST API.
    Implements required headers and signing for write operations.
    """

    def __init__(self):
        self.base_url = settings.bayse_base_url.rstrip("/")
        self.public_key = settings.bayse_public_key.strip().strip('"').strip("'")
        self.private_key = settings.bayse_private_key.strip().strip('"').strip("'")
        self.default_currency = settings.bayse_default_currency.strip().upper()
        self.mock_mode = settings.mock_mode
        self.client = httpx.AsyncClient(timeout=15.0)
        self.cache = TTLCache(maxsize=256, ttl=30)

    def minimum_order_amount(self, currency: str | None = None) -> float:
        curr = (currency or self.default_currency or "").strip().upper()
        if curr == "NGN":
            return 100.0
        return 1.0

    async def _request(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None, signed: bool = False):
        if self.mock_mode:
            return self._mock_response(path, method, params, json_body)

        url = f"{self.base_url}{path}"

        headers: Dict[str, str] = {}
        if signed or self.public_key:
            headers["X-Public-Key"] = self.public_key

        body_str = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False) if json_body else ""
        body_bytes = body_str.encode("utf-8") if body_str else b""
        if body_bytes:
            headers["Content-Type"] = "application/json"
        if signed:
            timestamp = str(int(time.time()))
            # Per Bayse docs: payload = "{timestamp}.{METHOD}.{/v1/path}.{bodyHash}"
            # For no-body requests (DELETE), bodyHash is empty — payload ends with a dot.
            base_path = self.base_url.replace("https://relay.bayse.markets", "").replace("http://relay.bayse.markets", "")
            sign_path = f"{base_path}{path.split('?')[0]}"
            body_hash = hashlib.sha256(body_bytes).hexdigest() if body_bytes else ""
            payload = f"{timestamp}.{method.upper()}.{sign_path}.{body_hash}"
            signature = base64.b64encode(
                hmac.new(self.private_key.encode(), payload.encode(), hashlib.sha256).digest()
            ).decode()
            logger.debug("Signing: %s body=%s", payload[:120], body_str)
            headers["X-Timestamp"] = timestamp
            headers["X-Signature"] = signature

        # Strip None values from params so they don't get serialised as "None"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None} or None

        for attempt in range(3):
            try:
                resp = await self.client.request(method, url, params=clean_params, content=body_bytes if body_bytes else None, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    await self._backoff(attempt)
                    continue
                if resp.status_code == 401:
                    # Auth failure — no point retrying, signing is wrong or keys are invalid
                    err_detail = ""
                    try:
                        err_detail = resp.json()
                    except Exception:
                        err_detail = resp.text[:500]
                    logger.error(
                        "Bayse 401 Unauthorized on %s %s — check API keys and HMAC signing. response=%s",
                        method,
                        path,
                        err_detail,
                    )
                    if signed:
                        raise BayseAuthError(f"Bayse 401 Unauthorized on {method} {path}")
                    return self._fallback_response(path, method, params, json_body)
                if resp.status_code == 400:
                    err_detail = ""
                    try:
                        err_detail = resp.json()
                    except Exception:
                        err_detail = resp.text[:500]
                    logger.error(
                        "Bayse 400 Bad Request on %s %s response=%s body=%s",
                        method,
                        path,
                        err_detail,
                        body_str,
                    )
                    raise BayseRequestError(f"Bayse 400 Bad Request on {method} {path}: {err_detail}")
                if resp.status_code == 422:
                    err_detail = ""
                    try:
                        err_detail = resp.json()
                    except Exception:
                        err_detail = resp.text[:500]
                    logger.error(
                        "Bayse 422 Unprocessable Entity on %s %s response=%s body=%s",
                        method,
                        path,
                        err_detail,
                        body_str,
                    )
                    raise BayseRequestError(f"Bayse 422 Unprocessable Entity on {method} {path}: {err_detail}")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                logger.warning("Bayse request error %s %s attempt=%s err=%s", method, path, attempt, exc)
                if attempt == 2:
                    if signed:
                        raise
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
    async def list_events(self, category: str | None = None, status: str | None = "open", keyword: str | None = None, page: int = 1, size: int = 20, trending: bool | None = None, series_slug: str | None = None):
        cache_key = ("events", category, status, keyword, page, size, trending, series_slug)
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
        if series_slug:
            params["seriesSlug"] = series_slug
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
        """
        Get ticker for a market outcome. Only works on CLOB markets.
        AMM markets return 400 — we return {} silently in that case.
        """
        params: Dict[str, Any] = {}
        if outcome:
            params["outcome"] = outcome
        if outcome_id:
            params["outcomeId"] = outcome_id
        if self.mock_mode:
            return {}
        url = f"{self.base_url}/pm/markets/{market_id}/ticker"
        headers: Dict[str, str] = {}
        if self.public_key:
            headers["X-Public-Key"] = self.public_key
        clean_params = {k: v for k, v in params.items() if v is not None} or None
        try:
            resp = await self.client.get(url, params=clean_params, headers=headers)
            if resp.status_code == 400:
                return {}  # AMM market — ticker not supported, no noise
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}

    async def trades(self, market_id: str, limit: int = 50):
        return await self._request("GET", "/pm/trades", params={"marketId": market_id, "limit": limit})

    async def get_wallet_balance(self, currency: str | None = None) -> float:
        """
        Fetch the actual spendable wallet balance from GET /wallet/assets.
        Returns the availableBalance for the configured currency (default NGN).
        This is the real cash balance, not the portfolio market value.
        """
        curr = (currency or self.default_currency).upper()
        try:
            data = await self._request("GET", "/wallet/assets")
            assets = (data or {}).get("assets", [])
            for asset in assets:
                if asset.get("symbol", "").upper() == curr:
                    return float(asset.get("availableBalance") or 0.0)
        except Exception as exc:
            logger.warning("get_wallet_balance failed: %s", exc)
        return 0.0

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

    async def quote(self, event_id: str, market_id: str, outcome_id: str, side: str, amount: float, currency: str | None = None):
        """Get a price quote. outcome_id is the UUID from market.outcome1Id / outcome2Id."""
        body = {
            "side": side,
            "outcomeId": outcome_id,
            "amount": amount,
            "currency": currency or self.default_currency,
        }
        return await self._request("POST", f"/pm/events/{event_id}/markets/{market_id}/quote", json_body=body)

    async def place_order(
        self,
        event_id: str,
        market_id: str,
        side: str,
        amount: float,
        outcome: str = "YES",
        outcome_id: str | None = None,
        order_type: str = "MARKET",
        currency: str = "NGN",
        price: float | None = None,
    ):
        """
        Place a buy or sell order.
        Bayse API requires outcomeId (UUID). If not provided, we fetch the event
        to resolve it from outcome1Id/outcome2Id based on the outcome label.
        """
        # Resolve outcomeId if not provided
        resolved_outcome_id = outcome_id
        if not resolved_outcome_id:
            try:
                ev = await self.get_event(event_id)
                for m in (ev or {}).get("markets", []):
                    if m.get("id") == market_id:
                        resolved_outcome_id = (
                            m.get("outcome1Id") if outcome.upper() == "YES"
                            else m.get("outcome2Id")
                        ) or ""
                        break
            except Exception:
                pass

        body: dict = {
            "side": side,
            "outcome": outcome.upper() if outcome else "YES",
            "outcomeId": resolved_outcome_id or "",
            "amount": amount,
            "type": order_type,
            "currency": currency,
        }
        if price is not None:
            body["price"] = price
        return await self._request(
            "POST",
            f"/pm/events/{event_id}/markets/{market_id}/orders",
            json_body=body,
            signed=True,
        )

    async def cancel_order(self, order_id: str):
        return await self._request("DELETE", f"/pm/orders/{order_id}", signed=True)

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
            return {
                "engine": "AMM",
                "order": {
                    "id": "mock-order-id",
                    "outcome": (body or {}).get("outcome", (body or {}).get("outcomeId", "YES")),
                    "side": (body or {}).get("side", "BUY"),
                    "type": "MARKET",
                    "status": "filled",
                    "amount": (body or {}).get("amount", 100),
                    "price": 0.55,
                    "quantity": (body or {}).get("amount", 100) / 0.55,
                    "currency": (body or {}).get("currency", "NGN"),
                },
            }
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


# Module-level singleton so all requests share one HTTP connection pool.
_bayse_client: BayseClient | None = None


def get_bayse_client() -> BayseClient:
    global _bayse_client
    if _bayse_client is None:
        _bayse_client = BayseClient()
    return _bayse_client
