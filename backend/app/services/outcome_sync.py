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

    # Calibration tracking: was the signal direction correct?
    signal.direction_correct = 1 if is_win else 0
    logger.info(
        "Calibration: market=%s signal=%s confidence=%d direction_correct=%d resolution=%s",
        signal.market_name or signal.market_id,
        signal.signal_type,
        signal.confidence or 0,
        signal.direction_correct,
        signal.resolution,
    )

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
            # Backfill direction_correct if not already set
            is_win = _is_win_for_signal(signal.signal_type, market_resolution)
            if is_win is not None and signal.direction_correct is None:
                signal.direction_correct = 1 if is_win else 0
                session.add(signal)
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
def _signal_resolution_from_event(event: dict) -> str | None:
    """Extract the market resolution from a Bayse event payload."""
    raw = event.get("resolution") or event.get("resolvedOutcome") or event.get("outcome")
    if raw is not None:
        normalized = str(raw).strip().upper()
        if normalized in {"YES", "NO", "WIN", "LOSS"}:
            return normalized
    for market in event.get("markets") or []:
        mr = market.get("resolution") or market.get("resolvedOutcome")
        if mr is not None:
            normalized = str(mr).strip().upper()
            if normalized in {"YES", "NO", "WIN", "LOSS"}:
                return normalized
    return None


async def sync_signal_outcome_only(
    session: AsyncSession,
    signal: Signal,
    *,
    market_resolution: str,
) -> bool:
    """
    Resolve a signal that was never executed — predicted but didn't bet.

    Returns True if the signal was resolved and fed into Bayes, False otherwise.
    """
    from app.config import settings as app_settings

    if signal.resolution in {"WIN", "LOSS"}:
        return False

    sig_type = str(signal.signal_type or "").strip().upper()

    # Only BUY_YES/BUY_NO/SELL have clear directional predictions to validate.
    # HOLD/AVOID: record the resolution for analytics but don't train Bayes.
    if sig_type not in ("BUY_YES", "BUY_NO", "BUY", "SELL"):
        signal.resolution = market_resolution
        signal.direction_correct = None
        session.add(signal)
        await session.commit()
        logger.info(
            "Signal %s (%s %s) recorded resolution=%s (non-directional)",
            signal.id, signal.signal_type, signal.market_name or signal.market_id,
            market_resolution,
        )
        return False

    resolution = str(market_resolution).strip().upper()
    is_win: bool | None = None

    if resolution in {"WIN", "LOSS"}:
        if sig_type in ("BUY_YES", "BUY"):
            is_win = resolution == "WIN"
        elif sig_type == "BUY_NO":
            is_win = resolution == "LOSS"
    elif resolution == "YES":
        if sig_type in ("BUY_YES", "BUY"):
            is_win = True
        elif sig_type == "BUY_NO":
            is_win = False
    elif resolution == "NO":
        if sig_type in ("BUY_YES", "BUY"):
            is_win = False
        elif sig_type == "BUY_NO":
            is_win = True

    if is_win is None:
        return False

    signal.resolution = "WIN" if is_win else "LOSS"
    signal.status = "WON" if is_win else "LOST"
    signal.direction_correct = 1 if is_win else 0

    # Bayes update with reduced weight for non-executed signals
    resolved_yes = _resolved_yes_for_signal(sig_type, market_resolution)
    if resolved_yes is not None:
        weight_multiplier = max(0.0, min(1.0, app_settings.signal_outcome_weight))
        if weight_multiplier > 0.0:
            state_key = getattr(signal, "bayes_state_key", None) or "default"
            existing_state = await get_bayes_state(session, state_key=state_key)
            bayes_model = BayesModel.from_counts(
                alpha=float(existing_state.prior_json.get("alpha", 1.0)) if existing_state and existing_state.prior_json else 1.0,
                beta=float(existing_state.prior_json.get("beta", 1.0)) if existing_state and existing_state.prior_json else 1.0,
                yes_updates=int(existing_state.yes_updates or 0) if existing_state else 0,
                no_updates=int(existing_state.no_updates or 0) if existing_state else 0,
                model_version=(existing_state.model_version if existing_state else "v1"),
            )
            base_weight = _resolution_weight(signal.confidence)
            effective_weight = base_weight * weight_multiplier
            bayes_model.update_from_resolution(resolved_yes, weight=effective_weight)
            await save_bayes_state(session, bayes_model.state, state_key=state_key)
            await refresh_backtest_snapshots(session, state_key=state_key)
            logger.info(
                "Bayes updated from non-executed signal %s: state_key=%s "
                "resolved_yes=%s weight=%.2f (base=%.2f x %.2f) prior=%.3f",
                signal.id, state_key, resolved_yes,
                effective_weight, base_weight, weight_multiplier,
                bayes_model.state.prior_yes,
            )

    session.add(signal)
    await session.commit()
    logger.info(
        "Signal %s (%s %s) resolved as %s | direction_correct=%d | non-executed",
        signal.id, signal.signal_type,
        signal.market_name or signal.market_id,
        signal.resolution, signal.direction_correct or -1,
    )
    return True


async def reconcile_unexecuted_signals(
    session: AsyncSession,
    client,
    *,
    max_signals: int = 50,
) -> dict:
    """
    Batch-reconcile pending (non-executed) signals against Bayse market outcomes.

    Fetches unresolved PENDING signals and checks whether their markets have resolved.
    """
    from sqlalchemy import select, and_

    result = await session.execute(
        select(Signal)
        .where(
            and_(
                Signal.status == "PENDING",
                Signal.resolution.is_(None),
                Signal.event_id.isnot(None),
                Signal.event_id != "",
            )
        )
        .order_by(Signal.created_at.desc())
        .limit(max_signals)
    )
    signals = result.scalars().all()

    if not signals:
        return {"scanned": 0, "resolved": 0, "skipped": 0}

    # Group by event_id to minimize API calls
    event_ids: set[str] = set()
    for s in signals:
        eid = (s.event_id or "").strip()
        if eid:
            event_ids.add(eid)

    event_cache: dict[str, dict] = {}
    for eid in event_ids:
        try:
            event_data = await client.get_event(eid)
            if event_data and isinstance(event_data, dict):
                event_cache[eid] = event_data
        except Exception as exc:
            logger.debug("Failed to fetch event %s for signal reconciliation: %s", eid, exc)

    resolved_count = 0
    skipped_count = 0
    for signal in signals:
        eid = (signal.event_id or "").strip()
        event = event_cache.get(eid)
        if not event:
            skipped_count += 1
            continue

        resolution = _signal_resolution_from_event(event)
        if not resolution:
            skipped_count += 1
            continue

        try:
            updated = await sync_signal_outcome_only(
                session, signal, market_resolution=resolution,
            )
            if updated:
                resolved_count += 1
            else:
                skipped_count += 1
        except Exception as exc:
            logger.warning("Failed to reconcile signal %s: %s", signal.id, exc, exc_info=True)
            skipped_count += 1

    return {
        "scanned": len(signals),
        "resolved": resolved_count,
        "skipped": skipped_count,
        "events_checked": len(event_cache),
    }
