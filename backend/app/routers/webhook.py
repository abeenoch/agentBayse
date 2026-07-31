
import secrets
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.outcome_sync import sync_signal_outcome
from app.utils.logger import logger
from app.websocket_manager import manager
from sqlalchemy import select

router = APIRouter()


def _normalize_label(value: object | None) -> str:
    text = str(value or "").strip().upper()
    return text.replace("-", "_").replace(" ", "_")


def _normalize_terminal_resolution(value: object | None) -> str | None:
    normalized = _normalize_label(value)
    if normalized in {"YES", "NO", "WIN", "LOSS"}:
        return normalized
    if normalized == "STOP_LOSS":
        return "LOSS"
    return None


def _normalize_order_status(value: object | None) -> str | None:
    normalized = _normalize_label(value)
    if normalized in {"FILLED", "RESOLVED", "EXECUTED"}:
        return "EXECUTED"
    if normalized in {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}:
        return "STALE"
    return None


async def apply_order_resolution(session, payload: dict) -> dict:
    """
    Apply a Bayse order-resolution webhook payload to the matching trade.

    This keeps webhook handling testable and idempotent while centralizing the
    settlement logic.
    """
    order_id = (
        payload.get("orderId")
        or payload.get("order_id")
        or payload.get("id")
    )
    event_id = payload.get("eventId") or payload.get("event_id")
    market_id = payload.get("marketId") or payload.get("market_id")
    if not order_id and not (event_id and market_id):
        return {"ok": True, "ignored": "missing_order_id"}

    resolution = payload.get("resolution") or payload.get("result")
    payout = payload.get("payout") or payload.get("payoutAmount")
    status = payload.get("status") or payload.get("orderStatus")

    normalized_resolution = _normalize_terminal_resolution(resolution)
    normalized_status = _normalize_order_status(status)

    logger.info(
        "Webhook: order=%s status=%s resolution=%s payout=%s",
        order_id, status, resolution, payout,
    )

    trade = None
    if order_id:
        result = await session.execute(
            select(Trade).where(Trade.bayse_order_id == order_id)
        )
        trade = result.scalars().first()
    if trade is None and event_id and market_id:
        result = await session.execute(
            select(Trade).where(Trade.market_id == str(market_id))
        )
        candidate_trades = result.scalars().all()
        for candidate in candidate_trades:
            if not candidate.signal_id:
                continue
            signal = await session.get(Signal, candidate.signal_id)
            if signal and str(signal.event_id or "") == str(event_id):
                trade = candidate
                break
    if not trade:
        logger.info("Webhook: no trade found for order %s", order_id)
        return {"ok": True, "matched": False}

    signal = None
    if trade.signal_id:
        signal = await session.get(Signal, trade.signal_id)

    if normalized_status:
        trade.status = normalized_status

    if normalized_resolution is None and payout is not None:
        inferred = "WIN" if float(payout) > float(trade.total_cost or 0) else "LOSS"
        normalized_resolution = inferred

    trade_resolution = normalized_resolution
    if signal and normalized_resolution in {"YES", "NO"}:
        signal_kind = _normalize_label(getattr(signal, "signal_type", ""))
        if signal_kind in {"BUY_YES", "BUY"}:
            trade_resolution = "WIN" if normalized_resolution == "YES" else "LOSS"
        elif signal_kind == "BUY_NO":
            trade_resolution = "LOSS" if normalized_resolution == "YES" else "WIN"

    if trade_resolution:
        if trade.status == "STALE":
            trade.status = "EXECUTED"
        trade.resolution = trade_resolution
        trade.resolved_at = trade.resolved_at or datetime.utcnow()
        if payout is not None:
            trade.pnl = float(payout) - float(trade.total_cost or 0)

    if signal and normalized_resolution:
            await sync_signal_outcome(
                session,
                trade,
                market_resolution=normalized_resolution,
                payout=float(payout) if payout is not None else None,
            )
            session.add(signal)

    session.add(trade)
    await session.commit()

    await manager.broadcast({
        "type": "order_resolved",
        "data": {
            "order_id": order_id,
            "resolution": normalized_resolution,
            "pnl": trade.pnl,
        },
    })
    return {
        "ok": True,
        "matched": True,
        "order_id": order_id,
        "resolution": normalized_resolution,
        "status": trade.status,
    }


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

    try:
        async with AsyncSessionLocal() as session:
            result = await apply_order_resolution(session, payload)
    except Exception as exc:
        order_id = payload.get("orderId") or payload.get("order_id") or payload.get("id")
        logger.error("Webhook processing failed for order %s: %s", order_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    return result
