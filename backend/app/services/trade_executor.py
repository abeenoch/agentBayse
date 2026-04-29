from datetime import datetime, date
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bayse_client import BayseClient
from app.models.signal import Signal
from app.models.trade import Trade
from app.utils.logger import logger


def _pick_outcome(signal: Signal) -> str:
    return "YES" if signal.signal_type in ("BUY_YES", "BUY", "YES") else "NO"


def _resolve_share_quantity(order: dict, amount: float, fallback_price: float) -> int:
    quantity = order.get("quantity") or order.get("size") or order.get("filledSize")
    if quantity is not None:
        try:
            return max(int(float(quantity)), 1)
        except Exception:
            pass
    price = float(order.get("price") or fallback_price or 0.0)
    if price > 0:
        return max(int(round(float(amount) / price)), 1)
    return 0


async def execute_signal(
    session: AsyncSession,
    client: BayseClient,
    signal: Signal,
    amount_override: float | None = None,
    event_data: dict | None = None,
):
    side = "BUY"
    outcome_label = _pick_outcome(signal)
    amount = amount_override or signal.suggested_stake
    event_id = (signal.event_id or "").strip() or signal.market_id
    currency = client.default_currency
    min_amount_fn = getattr(client, "minimum_order_amount", None)
    min_amount = min_amount_fn(currency) if callable(min_amount_fn) else (100.0 if currency.upper() == "NGN" else 1.0)
    if amount < min_amount:
        logger.info(
            "Raising order amount for market %s from %.2f to Bayse minimum %.2f %s",
            signal.market_id,
            amount,
            min_amount,
            currency,
        )
        amount = min_amount

    # Resolve outcomeId from event_data if available, otherwise fetch the event.
    outcome_id = None
    source_event = event_data
    if source_event is None:
        try:
            source_event = await client.get_event(event_id)
        except Exception:
            source_event = None

    if source_event:
        for m in (source_event.get("markets") or []):
            if m.get("id") == signal.market_id:
                outcome_id = (
                    m.get("outcome1Id") if outcome_label == "YES"
                    else m.get("outcome2Id")
                ) or None
                break

    resp = await client.place_order(
        event_id=event_id,
        market_id=signal.market_id,
        side=side,
        outcome=outcome_label,
        outcome_id=outcome_id,  # pass if known, client will fetch if None
        amount=amount,
        order_type="MARKET",
        currency=currency,
    )

    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response: {resp}")

    if "order" not in resp and "clobOrder" not in resp and resp.get("error"):
        raise RuntimeError(f"Bayse rejected order: {resp}")

    # Bayse returns order details under different keys depending on engine
    order = resp.get("order") or resp.get("clobOrder") or resp.get("ammOrder") or {}
    order_id = order.get("id")
    if not order_id:
        logger.warning("Bayse order response did not include an order id: %s", resp)

    signal.executed_order_id = order_id
    signal.status = "EXECUTED"
    signal.executed_at = datetime.utcnow()

    trade = Trade(
        market_id=signal.market_id,
        market_name=signal.market_name,
        side=side,
        shares=_resolve_share_quantity(order, amount, signal.market_price_at_signal),
        price=float(order.get("price") or signal.market_price_at_signal),
        total_cost=amount,
        status="EXECUTED",
        source="AGENT",
        signal_id=signal.id,
        bayse_order_id=order_id,
    )
    session.add(trade)
    await session.commit()
    await session.refresh(signal)

    try:
        from app.services.scheduler import ensure_order_monitor_job
        await ensure_order_monitor_job()
    except Exception:
        pass

    return resp


async def executed_today(session: AsyncSession) -> int:
    today = date.today()
    result = await session.execute(
        select(func.count()).select_from(Signal).where(
            Signal.executed_at.isnot(None),
            func.date(Signal.executed_at) == today,
        )
    )
    return result.scalar_one()
