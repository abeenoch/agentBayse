
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.signal import Signal
from app.models.trade import Trade
from app.utils.logger import logger
from app.websocket_manager import manager
from sqlalchemy import select

router = APIRouter()


@router.post("/order")
async def order_webhook(request: Request, x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret")):
    """
    Receive order resolution event from Bayse.
    Expected payload (best-effort — handle whatever Bayse sends):
      {
        "orderId": "...",
        "status": "RESOLVED" | "FILLED" | ...,
        "resolution": "YES" | "NO" | ...,
        "payout": 1234.56,
        "marketId": "...",
      }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    if not x_webhook_secret or not secrets.compare_digest(x_webhook_secret, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    order_id = (
        payload.get("orderId")
        or payload.get("order_id")
        or payload.get("id")
    )
    if not order_id:
        # Not actionable — just ack
        return {"ok": True}

    resolution = payload.get("resolution") or payload.get("result")
    payout = payload.get("payout") or payload.get("payoutAmount")
    status = payload.get("status") or payload.get("orderStatus")

    logger.info(
        "Webhook: order=%s status=%s resolution=%s payout=%s",
        order_id, status, resolution, payout,
    )

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.bayse_order_id == order_id)
            )
            trade = result.scalars().first()
            if not trade:
                logger.info("Webhook: no trade found for order %s", order_id)
                return {"ok": True}

            if status:
                trade.status = status
            if resolution:
                trade.resolution = resolution
            if payout is not None:
                trade.pnl = float(payout) - (trade.total_cost or 0)

            if trade.signal_id:
                sig = await session.get(Signal, trade.signal_id)
                if sig:
                    if resolution:
                        sig.resolution = resolution
                        sig.status = "WON" if (payout or 0) > (trade.total_cost or 0) else "LOST"
                    if payout is not None:
                        sig.pnl = float(payout) - (trade.total_cost or 0)
                    session.add(sig)

            session.add(trade)
            await session.commit()

            await manager.broadcast({
                "type": "order_resolved",
                "data": {
                    "order_id": order_id,
                    "resolution": resolution,
                    "pnl": trade.pnl,
                },
            })
    except Exception as exc:
        logger.error("Webhook processing failed for order %s: %s", order_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    return {"ok": True}
