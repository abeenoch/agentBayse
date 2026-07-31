from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.database import AsyncSessionLocal
from app.services.bayse_client import get_bayse_client
from app.services.scheduler import normalize_terminal_trades, reconcile_open_trades, start_scheduler
from app.routers import auth, markets, trades, portfolio, agent, search, websocket, webhook


def create_app() -> FastAPI:
    app = FastAPI(title="Bayse AI Trading Agent", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(markets.router, prefix="/markets", tags=["markets"])
    app.include_router(trades.router, prefix="/trades", tags=["trades"])
    app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
    app.include_router(agent.router, prefix="/agent", tags=["agent"])
    app.include_router(search.router, prefix="/search", tags=["search"])
    app.include_router(websocket.router, tags=["websocket"])
    app.include_router(webhook.router, prefix="/webhook", tags=["webhook"])

    async def run_startup_reconciliation() -> None:
        await init_db()
        async with AsyncSessionLocal() as session:
            normalized_count = await normalize_terminal_trades(session)
            checked_count, resolved_count = await reconcile_open_trades(session, get_bayse_client())
            from app.utils.logger import logger
            logger.info(
                "Startup reconciliation complete: normalized=%d checked=%d resolved=%d",
                normalized_count,
                checked_count,
                resolved_count,
            )

    @app.on_event("startup")
    async def startup_event():
        try:
            await run_startup_reconciliation()
        except Exception as exc:
            # Startup should continue even if Bayse is temporarily unreachable.
            from app.utils.logger import logger
            logger.warning("Startup reconciliation failed: %s", exc, exc_info=True)
        start_scheduler()

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "bayse_api": "unknown",
            "db": "ok",
            "agent": "running",
        }

    return app


app = create_app()
