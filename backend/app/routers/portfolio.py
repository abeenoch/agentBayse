from fastapi import APIRouter, Depends

from app.services.bayse_client import BayseClient, get_bayse_client
from app.dependencies import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("")
async def get_portfolio(
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.get_portfolio()


@router.get("/orders")
async def get_orders(
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.list_orders()


@router.get("/activities")
async def get_activities(
    type: str | None = None,
    page: int = 1,
    size: int = 20,
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.get_activities(type=type, page=page, size=size)


@router.get("/positions")
async def get_open_positions(
    client: BayseClient = Depends(get_bayse_client),
):
    """Return current open positions directly from Bayse outcomeBalances."""
    portfolio = await client.get_portfolio() or {}
    balances = portfolio.get("outcomeBalances") or []
    positions = []
    for b in balances:
        if not b:
            continue
        market = b.get("market") or {}
        event = market.get("event") or {}
        positions.append({
            "market_id": market.get("id") or b.get("marketId"),
            "market_name": event.get("title") or market.get("title") or "Unknown",
            "outcome": b.get("outcome") or b.get("side"),
            "quantity": b.get("quantity") or b.get("size"),
            "avg_price": b.get("avgPrice") or b.get("price"),
            "current_value": b.get("currentValue") or b.get("value"),
            "cost": b.get("cost") or b.get("totalCost"),
            "pnl": b.get("pnl") or b.get("unrealizedPnl"),
            "pnl_pct": b.get("percentageChange") or b.get("pnlPct"),
        })
    return {"positions": positions, "count": len(positions)}


@router.get("/assets")
async def get_assets(client: BayseClient = Depends(get_bayse_client)):
    return await client.get_assets()
