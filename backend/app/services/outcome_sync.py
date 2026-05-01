from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal
from app.models.trade import Trade


def _normalize_terminal_result(value: str | None) -> str | None:
    if not value:
        return None

    normalized = str(value).strip().upper()
    if normalized in {"WIN", "LOSS"}:
        return normalized

    # STOP_LOSS is a realized loss for the executed position.
    if normalized == "STOP_LOSS":
        return "LOSS"

    return None


def _is_win_for_signal(signal_type: str | None, market_resolution: str | None) -> bool | None:
    normalized = _normalize_terminal_result(market_resolution)
    if normalized is not None:
        return normalized == "WIN"

    if not market_resolution or not signal_type:
        return None

    resolution = str(market_resolution).strip().upper()
    signal_kind = str(signal_type).strip().upper()

    if resolution not in {"YES", "NO"}:
        return None

    if signal_kind in {"BUY_YES", "BUY"}:
        return resolution == "YES"

    if signal_kind == "BUY_NO":
        return resolution == "NO"

    return None


async def sync_signal_outcome(
    session: AsyncSession,
    trade: Trade,
    *,
    market_resolution: str | None = None,
    payout: float | None = None,
) -> Signal | None:
    """
    Copy a known trade outcome onto the linked signal.

    Returns the updated Signal when a linked signal exists and the outcome can
    be interpreted, otherwise returns None.
    """
    if not trade.signal_id:
        return None

    signal = await session.get(Signal, trade.signal_id)
    if not signal:
        return None

    is_win = _is_win_for_signal(signal.signal_type, market_resolution)
    if is_win is None:
        return None

    signal.resolution = "WIN" if is_win else "LOSS"
    signal.status = "WON" if is_win else "LOST"

    if payout is not None:
        signal.pnl = float(payout) - float(trade.total_cost or 0.0)

    session.add(signal)
    return signal
