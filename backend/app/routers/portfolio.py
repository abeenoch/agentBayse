from fastapi import APIRouter, Depends

from app.services.bayse_client import BayseClient, get_bayse_client

router = APIRouter()


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


@router.get("/assets")
async def get_assets(client: BayseClient = Depends(get_bayse_client)):
    return await client.get_assets()
