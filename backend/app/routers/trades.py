from fastapi import APIRouter, Depends

from app.services.bayse_client import BayseClient, get_bayse_client

router = APIRouter()


@router.post("")
async def place_trade(
    event_id: str,
    market_id: str,
    side: str,
    outcome: str,
    amount: float,
    currency: str = "NGN",
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.place_order(event_id, market_id, side=side, outcome=outcome, amount=amount, currency=currency)


@router.get("")
async def list_orders(
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    client: BayseClient = Depends(get_bayse_client),
):
    return await client.list_orders(status=status, page=page, size=size)


@router.delete("/{order_id}")
async def cancel_order(order_id: str, client: BayseClient = Depends(get_bayse_client)):
    return await client.cancel_order(order_id)
