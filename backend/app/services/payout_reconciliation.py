from __future__ import annotations

from datetime import datetime
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal
from app.models.trade import Trade
from app.services.outcome_sync import sync_signal_outcome


def _activity_sort_key(activity: dict[str, Any]) -> str:
    return str(activity.get("createdAt") or activity.get("updatedAt") or "")


def _normalize_activity_label(value: Any) -> str:
    """
    Normalize Bayse activity labels that may vary in punctuation/casing.

    Examples:
      - "PAYOUT WIN" -> "PAYOUT_WIN"
      - "payout-loss" -> "PAYOUT_LOSS"
      - " yes " -> "YES"
    """
    text = str(value or "").strip().upper()
    return text.replace("-", "_").replace(" ", "_")


def index_payout_activities(
    activities: Iterable[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """
    Index payout activities by the keys we can actually match on.

    Bayse does not currently send orderId in the payout feed, so we keep a
    fallback index on (eventId, marketId).
    """
    by_order: dict[str, dict[str, Any]] = {}
    by_event_market: dict[tuple[str, str], dict[str, Any]] = {}

    for activity in activities:
        order_id = activity.get("orderId")
        if order_id:
            by_order[str(order_id)] = activity

        event_id = activity.get("eventId")
        market_id = activity.get("marketId")
        if not event_id or not market_id:
            continue

        key = (str(event_id), str(market_id))
        existing = by_event_market.get(key)
        if existing is None or _activity_sort_key(activity) >= _activity_sort_key(existing):
            by_event_market[key] = activity

    return by_order, by_event_market


async def match_payout_activity_for_trade(
    session: AsyncSession,
    trade: Trade,
    payout_by_order: dict[str, dict[str, Any]],
    payout_by_event_market: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, Any] | None, Signal | None]:
    """
    Match a payout activity to a trade using the best available key.

    Preference order:
      1. Bayse orderId, if present.
      2. Trade-linked signal eventId + marketId.
    """
    signal: Signal | None = None
    if trade.signal_id:
        signal = await session.get(Signal, trade.signal_id)

    if trade.bayse_order_id:
        activity = payout_by_order.get(str(trade.bayse_order_id))
        if activity:
            return activity, signal

    if signal and signal.event_id:
        activity = payout_by_event_market.get((str(signal.event_id), str(trade.market_id)))
        if activity:
            return activity, signal

    return None, signal


def activity_outcome(activity: dict[str, Any]) -> tuple[str | None, float | None]:
    """
    Convert a payout activity into a terminal trade outcome and payout amount.

    Returns:
      - market_resolution: WIN | LOSS | YES | NO | None
      - payout: numeric payout if present, otherwise None
    """
    act_type = _normalize_activity_label(activity.get("type"))
    resolved_outcome = _normalize_activity_label(activity.get("resolvedOutcome"))
    payout_raw = activity.get("payout")
    payout = None
    if payout_raw is not None:
        try:
            payout = float(payout_raw)
        except Exception:
            payout = None

    if act_type in {"PAYOUT_WIN", "WIN"}:
        return "WIN", payout if payout is not None else 0.0
    if act_type in {"PAYOUT_LOSS", "LOSS"}:
        return "LOSS", payout if payout is not None else 0.0

    if resolved_outcome in {"YES", "NO", "WIN", "LOSS"}:
        return resolved_outcome, payout

    return None, payout


async def apply_activity_to_trade(
    session: AsyncSession,
    trade: Trade,
    activity: dict[str, Any],
) -> Signal | None:
    """
    Apply a payout activity to a trade and copy the outcome onto the linked signal.
    """
    market_resolution, payout = activity_outcome(activity)
    if market_resolution is None:
        return None

    terminal_outcome = _normalize_activity_label(activity.get("type"))
    if terminal_outcome in {"PAYOUT_WIN", "WIN"}:
        trade.resolution = "WIN"
        trade.pnl = (payout or 0.0) - (trade.total_cost or 0)
    elif terminal_outcome in {"PAYOUT_LOSS", "LOSS"}:
        trade.resolution = "LOSS"
        trade.pnl = -(trade.total_cost or 0)
    if trade.status == "STALE":
        trade.status = "EXECUTED"
    trade.resolved_at = trade.resolved_at or datetime.utcnow()

    session.add(trade)
    signal = await sync_signal_outcome(
        session,
        trade,
        market_resolution=market_resolution,
        payout=payout,
    )

    if signal and trade.resolution is None and signal.resolution in {"WIN", "LOSS"}:
        trade.resolution = signal.resolution
        if signal.resolution == "WIN":
            trade.pnl = (payout or 0.0) - (trade.total_cost or 0)
        elif payout is not None:
            trade.pnl = payout - (trade.total_cost or 0)
        else:
            trade.pnl = -(trade.total_cost or 0)
        session.add(trade)

    return signal
