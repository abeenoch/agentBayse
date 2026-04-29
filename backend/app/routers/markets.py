from fastapi import APIRouter, Depends, Query

from app.services.bayse_client import BayseClient, get_bayse_client

router = APIRouter()


@router.get("")
async def list_markets(
    category: str | None = None,
    status: str | None = "open",
    keyword: str | None = None,
    page: int = 1,
    size: int = 50,
    client: BayseClient = Depends(get_bayse_client),
):
    # Enforce finance-only scope by default so stray callers don't pull other categories.
    effective_category = category or "finance"
    return await client.list_events(
        category=effective_category,
        status=status,
        keyword=keyword,
        page=page,
        size=size,
    )


# doesn't swallow them as path-param matches.

@router.get("/trending")
async def trending(client: BayseClient = Depends(get_bayse_client)):
    return await client.list_events(trending=True)


@router.get("/series")
async def list_series(page: int = 1, size: int = 50, client: BayseClient = Depends(get_bayse_client)):
    return await client.list_series(page=page, size=size)


@router.get("/orderbook")
async def order_book(
    outcomeIds: list[str] = Query(..., alias="outcomeId[]"),
    depth: int = 10,
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.order_book(outcomeIds, depth=depth)


@router.get("/slug/{slug}")
async def get_event_by_slug(slug: str, client: BayseClient = Depends(get_bayse_client)):
    return await client.get_event_by_slug(slug)


@router.get("/{event_id}/price-history")
async def price_history(
    event_id: str,
    timePeriod: str = "24H",
    outcome: str | None = "YES",
    marketIds: list[str] = Query(None, alias="marketId[]"),
    client: BayseClient = Depends(get_bayse_client),
):
    data = await client.price_history(event_id, time_period=timePeriod, outcome=outcome, market_ids=marketIds)
    if marketIds:
        return {mid: data.get(mid, []) for mid in marketIds}
    return data


@router.get("/{market_id}/ticker")
async def ticker(
    market_id: str,
    outcome: str | None = None,
    outcomeId: str | None = None,
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.ticker(market_id, outcome=outcome, outcome_id=outcomeId)


@router.get("/{market_id}/trades")
async def recent_trades(
    market_id: str,
    limit: int = 20,
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.trades(market_id, limit=limit)


@router.get("/{event_id}")
async def get_event(event_id: str, client: BayseClient = Depends(get_bayse_client)):
    return await client.get_event(event_id)
