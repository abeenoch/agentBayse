from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.bayes_model import BayesModel
from app.services.bayes_backtest import refresh_backtest_snapshots
from app.services.config_service import get_config
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.storage import get_bayes_state, link_feature_snapshot, save_bayes_state
from app.utils.logger import logger


def _resolved_yes_for_signal(signal_type: str | None, market_resolution: str | None) -> bool | None:
    if not market_resolution or not signal_type:
        return None

    resolution = _normalize_terminal_result(market_resolution)
    signal_kind = str(signal_type).strip().upper()

    if resolution not in {"WIN", "LOSS"}:
        raw_resolution = str(market_resolution).strip().upper()
        if raw_resolution in {"YES", "NO"}:
            return raw_resolution == "YES"

    if resolution in {"WIN", "LOSS"} and signal_kind in {"BUY_YES", "BUY_NO", "BUY"}:
        if signal_kind in {"BUY_YES", "BUY"}:
            return resolution == "WIN"
        if signal_kind == "BUY_NO":
            return resolution == "LOSS"

    return None


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


def _resolution_weight(confidence: int | float | None) -> float:
    """
    Mirror the weight used by the live resolution sync path.
    """
    return max(0.5, min(2.0, 0.5 + (float(confidence or 0) / 100.0) * 1.5))


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

    # Avoid double-applying the same resolution if the webhook / monitor retries.
    if signal.resolution in {"WIN", "LOSS"}:
        return signal

    cfg = await get_config(session)
    default_state_key = getattr(cfg, "bayes_state_key", "default") or "default"
    state_key = (
        getattr(trade, "bayes_state_key", None)
        or getattr(signal, "bayes_state_key", None)
        or default_state_key
    )

    is_win = _is_win_for_signal(signal.signal_type, market_resolution)
    if is_win is None:
        return None

    if trade.status == "STALE":
        trade.status = "EXECUTED"
    trade.bayes_state_key = trade.bayes_state_key or state_key
    signal.bayes_state_key = signal.bayes_state_key or state_key
    signal.resolution = "WIN" if is_win else "LOSS"
    signal.status = "WON" if is_win else "LOST"
    trade.resolved_at = trade.resolved_at or datetime.utcnow()

    if payout is not None:
        signal.pnl = float(payout) - float(trade.total_cost or 0.0)

    session.add(signal)

    resolved_yes = _resolved_yes_for_signal(signal.signal_type, market_resolution)
    if resolved_yes is not None:
        existing_state = await get_bayes_state(session, state_key=state_key)
        bayes_model = BayesModel.from_counts(
            alpha=float(existing_state.prior_json.get("alpha", 1.0)) if existing_state and existing_state.prior_json else 1.0,
            beta=float(existing_state.prior_json.get("beta", 1.0)) if existing_state and existing_state.prior_json else 1.0,
            yes_updates=int(existing_state.yes_updates or 0) if existing_state else 0,
            no_updates=int(existing_state.no_updates or 0) if existing_state else 0,
            model_version=(existing_state.model_version if existing_state else "v1"),
        )
        confidence = max(0.5, min(2.0, 0.5 + (float(signal.confidence or 0) / 100.0) * 1.5))
        bayes_model.update_from_resolution(resolved_yes, weight=confidence)
        await save_bayes_state(session, bayes_model.state, state_key=state_key)
        await refresh_backtest_snapshots(session, state_key=state_key)
        await link_feature_snapshot(
            session,
            market_id=trade.market_id,
            signal_id=signal.id,
            trade_id=trade.id,
        )
        logger.info(
            "Bayes state updated from resolution: state_key=%s resolved_yes=%s weight=%.2f priors=%.3f yes_updates=%d no_updates=%d",
            state_key,
            resolved_yes,
            confidence,
            bayes_model.state.prior_yes,
            bayes_model.state.yes_updates,
            bayes_model.state.no_updates,
        )

    return signal


async def rebuild_bayes_state_from_resolved_trades(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> dict:
    """
    Rebuild Bayes state from resolved trade history.

    This is idempotent: it recomputes the state from scratch instead of applying
    incremental deltas, so rerunning it will not double-count the same trades.
    """
    result = await session.execute(
        select(Trade).where(Trade.resolution.in_(["WIN", "LOSS"]))
    )
    trades = list(result.scalars().all())

    cfg = await get_config(session)
    default_state_key = getattr(cfg, "bayes_state_key", "default") or "default"

    grouped: dict[str, list[tuple[Trade, Signal]]] = {}
    skipped = 0
    for trade in trades:
        if not trade.signal_id:
            skipped += 1
            continue
        signal = await session.get(Signal, trade.signal_id)
        if not signal:
            skipped += 1
            continue
        key = getattr(trade, "bayes_state_key", None) or getattr(signal, "bayes_state_key", None) or default_state_key
        if state_key and key != state_key:
            continue
        grouped.setdefault(key, []).append((trade, signal))

    summaries: dict[str, dict] = {}
    total_applied = 0
    for key, items in grouped.items():
        alpha = 1.0
        beta = 1.0
        yes_updates = 0
        no_updates = 0
        applied = 0
        for trade, signal in items:
            market_resolution = trade.resolution or signal.resolution
            resolved_yes = _resolved_yes_for_signal(signal.signal_type, market_resolution)
            if resolved_yes is None:
                continue
            weight = _resolution_weight(signal.confidence)
            if resolved_yes:
                alpha += weight
                yes_updates += 1
            else:
                beta += weight
                no_updates += 1
            applied += 1

        existing_state = await get_bayes_state(session, state_key=key)
        model = BayesModel.from_counts(
            alpha=alpha,
            beta=beta,
            yes_updates=yes_updates,
            no_updates=no_updates,
            model_version=(existing_state.model_version if existing_state else "v1"),
        )
        await save_bayes_state(session, model.state, state_key=key)
        await refresh_backtest_snapshots(session, state_key=key)
        summaries[key] = {
            "state_key": key,
            "scanned": len(items),
            "applied": applied,
            "yes_updates": yes_updates,
            "no_updates": no_updates,
            "prior_yes": model.state.prior_yes,
            "alpha": model.state.alpha,
            "beta": model.state.beta,
        }
        total_applied += applied
        logger.info(
            "Bayes rebuild complete: state_key=%s scanned=%d applied=%d yes_updates=%d no_updates=%d prior_yes=%.3f",
            key,
            len(items),
            applied,
            yes_updates,
            no_updates,
            model.state.prior_yes,
        )

    total_yes_updates = sum(summary["yes_updates"] for summary in summaries.values())
    total_no_updates = sum(summary["no_updates"] for summary in summaries.values())
    first_summary = next(iter(summaries.values()), None)
    return {
        "state_key": state_key or "all",
        "scanned": len(trades),
        "applied": total_applied,
        "skipped": skipped,
        "states": summaries,
        "yes_updates": total_yes_updates,
        "no_updates": total_no_updates,
        "prior_yes": first_summary["prior_yes"] if first_summary else None,
        "alpha": first_summary["alpha"] if first_summary else None,
        "beta": first_summary["beta"] if first_summary else None,
    }
