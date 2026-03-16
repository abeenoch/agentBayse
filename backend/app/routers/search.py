from fastapi import APIRouter, Query, Depends

from app.services.web_search import WebSearchService, get_search_service

router = APIRouter()


@router.get("")
async def search(q: str = Query(..., min_length=2), service: WebSearchService = Depends(get_search_service)):
    return await service.search(q)
