from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bayes_backtest_snapshot import BayesBacktestSnapshot
from app.models.market_snapshot import MarketSnapshot
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.analysis import calculate_max_drawdown
from app.websocket_manager import manager


def _normalize_price(value: float | None) -> float:
    if value is None:
        return 0.0
    price = float(value)
    if price > 1.0:
        price /= 100.0
    return max(0.0, min(1.0, price))


def _scale_liquidity(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 10000.0) / 10000.0)


def _scale_volume(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 50000.0) / 50000.0)


def _scale_orders(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 250.0) / 250.0)


def _scale_age_seconds(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 86400.0) / 86400.0)


def _is_win_for_signal(signal_type: str | None, resolution: str | None) -> bool | None:
    if not signal_type or not resolution:
        return None
    side = str(signal_type).strip().upper()
    outcome = str(resolution).strip().upper()
    if outcome not in {"WIN", "LOSS"}:
        return None
    if side == "BUY_YES":
        return outcome == "WIN"
    if side == "BUY_NO":
        return outcome == "LOSS"
    return None


@dataclass
class BacktestRow:
    created_at: datetime
    resolved_at: datetime | None
    trade_id: str
    signal_id: str | None
    market_id: str
    market_name: str
    bayes_state_key: str
    signal_type: str
    confidence: int
    estimated_probability: float
    market_price_at_signal: float
    expected_value: float
    rank_score: float | None
    resolution: str
    pnl: float
    market_snapshot_created_at: datetime | None = None
    market_liquidity: float | None = None
    market_volume: float | None = None
    market_orders: float | None = None
    market_spread: float | None = None
    market_imbalance: float | None = None
    snapshot_age_seconds: float | None = None

    @property
    def edge(self) -> float:
        return self.estimated_probability - _normalize_price(self.market_price_at_signal)

    @property
    def bucket_date(self) -> str:
        resolved = self.resolved_at or self.created_at
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).date().isoformat()


def _serialize_row(row: BacktestRow) -> dict[str, Any]:
    data = asdict(row)
    data["created_at"] = row.created_at.isoformat()
    data["resolved_at"] = row.resolved_at.isoformat() if row.resolved_at else None
    data["edge"] = row.edge
    return data


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _snapshot_key(snapshot: MarketSnapshot) -> tuple[datetime, str]:
    created_at = snapshot.created_at or datetime.min
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc), str(snapshot.id)


def _normalize_snapshot_time(value: datetime | None) -> datetime:
    dt = value or datetime.min
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def load_market_snapshot_index(
    session: AsyncSession,
    *,
    market_ids: set[str],
) -> dict[str, list[MarketSnapshot]]:
    if not market_ids:
        return {}
    result = await session.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id.in_(sorted(market_ids)))
        .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.created_at.asc())
    )
    index: dict[str, list[MarketSnapshot]] = {}
    for snapshot in result.scalars().all():
        index.setdefault(snapshot.market_id, []).append(snapshot)
    return index


def select_market_snapshot(
    snapshot_index: dict[str, list[MarketSnapshot]],
    *,
    market_id: str,
    as_of: datetime | None,
) -> MarketSnapshot | None:
    snapshots = snapshot_index.get(market_id) or []
    if not snapshots:
        return None
    if as_of is None:
        return snapshots[-1]
    target = _normalize_snapshot_time(as_of)
    times = [_snapshot_key(snapshot)[0] for snapshot in snapshots]
    idx = bisect_right(times, target) - 1
    if idx >= 0:
        return snapshots[idx]
    return snapshots[-1]


def snapshot_context_features(
    snapshot: MarketSnapshot | None,
    *,
    as_of: datetime | None,
) -> dict[str, float]:
    if snapshot is None:
        return {
            "market_liquidity": 0.0,
            "market_volume": 0.0,
            "market_orders": 0.0,
            "market_spread": 0.0,
            "market_imbalance": 0.0,
            "snapshot_age_seconds": 0.0,
        }

    outcome1 = _normalize_price(snapshot.outcome1_price)
    outcome2 = _normalize_price(snapshot.outcome2_price if snapshot.outcome2_price is not None else 1.0 - outcome1)
    created_at = _normalize_snapshot_time(snapshot.created_at)
    now = _normalize_snapshot_time(as_of)
    age_seconds = max(0.0, (now - created_at).total_seconds())

    return {
        "market_liquidity": _scale_liquidity(snapshot.liquidity),
        "market_volume": _scale_volume(snapshot.total_volume),
        "market_orders": _scale_orders(snapshot.total_orders),
        "market_spread": _normalize_price(abs(outcome1 - outcome2)),
        "market_imbalance": max(-1.0, min(1.0, outcome1 - outcome2)),
        "snapshot_age_seconds": _scale_age_seconds(age_seconds),
    }


async def load_backtest_rows(
    session: AsyncSession,
    *,
    state_key: str | None = None,
    limit: int | None = None,
) -> list[BacktestRow]:
    query = (
        select(Trade, Signal)
        .join(Signal, Signal.id == Trade.signal_id)
        .where(
            Trade.resolution.in_(["WIN", "LOSS"]),
            Signal.signal_type.in_(["BUY_YES", "BUY_NO"]),
        )
        .order_by(Trade.created_at.asc())
    )
    if limit is not None:
        query = query.limit(limit)

    result = await session.execute(query)
    raw_rows = list(result.all())
    market_ids = {trade.market_id for trade, _ in raw_rows if trade.market_id}
    snapshot_index = await load_market_snapshot_index(session, market_ids=market_ids)

    rows: list[BacktestRow] = []
    for trade, signal in raw_rows:
        row_state_key = getattr(trade, "bayes_state_key", None) or getattr(signal, "bayes_state_key", None) or "default"
        if state_key and row_state_key != state_key:
            continue
        as_of = trade.created_at or signal.created_at or datetime.utcnow()
        snapshot = select_market_snapshot(snapshot_index, market_id=trade.market_id, as_of=as_of)
        snapshot_features = snapshot_context_features(snapshot, as_of=as_of)
        rows.append(
            BacktestRow(
                created_at=as_of,
                resolved_at=trade.resolved_at or signal.executed_at or trade.executed_at,
                trade_id=str(trade.id),
                signal_id=str(signal.id) if signal else None,
                market_id=trade.market_id,
                market_name=trade.market_name,
                bayes_state_key=row_state_key,
                signal_type=signal.signal_type,
                confidence=int(signal.confidence or 0),
                estimated_probability=float(signal.estimated_probability or 0.0),
                market_price_at_signal=float(signal.market_price_at_signal or 0.0),
                expected_value=float(signal.expected_value or 0.0),
                rank_score=float(signal.rank_score or 0.0),
                resolution=trade.resolution or "UNKNOWN",
                pnl=float(trade.pnl or 0.0),
                market_snapshot_created_at=snapshot.created_at if snapshot else None,
                market_liquidity=snapshot_features["market_liquidity"],
                market_volume=snapshot_features["market_volume"],
                market_orders=snapshot_features["market_orders"],
                market_spread=snapshot_features["market_spread"],
                market_imbalance=snapshot_features["market_imbalance"],
                snapshot_age_seconds=snapshot_features["snapshot_age_seconds"],
            )
        )
    return rows


async def build_yes_no_audit(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> dict[str, Any]:
    query = (
        select(
            Signal.signal_type,
            Signal.confidence,
            Signal.estimated_probability,
            Signal.market_price_at_signal,
            Signal.expected_value,
            Signal.rank_score,
            Signal.bayes_state_key,
            Trade.resolution,
            Trade.pnl,
        )
        .select_from(Signal)
        .outerjoin(Trade, Trade.signal_id == Signal.id)
        .where(Signal.signal_type.in_(["BUY_YES", "BUY_NO"]))
    )
    if state_key:
        query = query.where(func.coalesce(Trade.bayes_state_key, Signal.bayes_state_key) == state_key)

    result = await session.execute(query.order_by(Signal.created_at.asc()))
    rows = result.all()

    side_stats: dict[str, dict[str, Any]] = {
        "BUY_YES": {
            "signal_type": "BUY_YES",
            "count": 0,
            "resolved_count": 0,
            "wins": 0,
            "losses": 0,
            "unresolved_count": 0,
            "confidence_sum": 0.0,
            "expected_value_sum": 0.0,
            "probability_sum": 0.0,
            "price_sum": 0.0,
            "pnl_sum": 0.0,
            "edge_sum": 0.0,
        },
        "BUY_NO": {
            "signal_type": "BUY_NO",
            "count": 0,
            "resolved_count": 0,
            "wins": 0,
            "losses": 0,
            "unresolved_count": 0,
            "confidence_sum": 0.0,
            "expected_value_sum": 0.0,
            "probability_sum": 0.0,
            "price_sum": 0.0,
            "pnl_sum": 0.0,
            "edge_sum": 0.0,
        },
    }

    total_signals = 0
    total_resolved = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for signal_type, confidence, estimated_probability, market_price_at_signal, expected_value, _rank_score, _row_state_key, resolution, pnl in rows:
        side = str(signal_type or "").upper()
        if side not in side_stats:
            continue
        stats = side_stats[side]
        stats["count"] += 1
        total_signals += 1

        conf = float(confidence or 0.0)
        prob = float(estimated_probability or 0.0)
        price = float(market_price_at_signal or 0.0)
        ev = float(expected_value or 0.0)
        stats["confidence_sum"] += conf
        stats["expected_value_sum"] += ev
        stats["probability_sum"] += prob
        stats["price_sum"] += price
        stats["edge_sum"] += prob - _normalize_price(price)

        is_win = _is_win_for_signal(side, resolution)
        if is_win is None:
            stats["unresolved_count"] += 1
            continue

        stats["resolved_count"] += 1
        total_resolved += 1
        trade_pnl = float(pnl or 0.0)
        stats["pnl_sum"] += trade_pnl
        total_pnl += trade_pnl
        if is_win:
            stats["wins"] += 1
            total_wins += 1
        else:
            stats["losses"] += 1
            total_losses += 1

    def _finalize(stats: dict[str, Any]) -> dict[str, Any]:
        count = max(int(stats["count"]), 1)
        resolved = int(stats["resolved_count"])
        return {
            "signal_type": stats["signal_type"],
            "count": stats["count"],
            "resolved_count": resolved,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "unresolved_count": stats["unresolved_count"],
            "win_rate": (stats["wins"] / resolved) if resolved else 0.0,
            "avg_confidence": stats["confidence_sum"] / count,
            "avg_expected_value": stats["expected_value_sum"] / count,
            "avg_probability": stats["probability_sum"] / count,
            "avg_market_price": stats["price_sum"] / count,
            "avg_edge": stats["edge_sum"] / count,
            "avg_pnl": (stats["pnl_sum"] / resolved) if resolved else 0.0,
            "total_pnl": stats["pnl_sum"],
        }

    yes = _finalize(side_stats["BUY_YES"])
    no = _finalize(side_stats["BUY_NO"])

    return {
        "state_key": state_key or "default",
        "total_signals": total_signals,
        "resolved_trades": total_resolved,
        "wins": total_wins,
        "losses": total_losses,
        "total_pnl": total_pnl,
        "yes": yes,
        "no": no,
        "signal_share": {
            "yes": (yes["count"] / total_signals) if total_signals else 0.0,
            "no": (no["count"] / total_signals) if total_signals else 0.0,
        },
        "side_bias": "NO" if no["count"] > yes["count"] else ("YES" if yes["count"] > no["count"] else "BALANCED"),
    }


async def get_cached_backtest_snapshot(
    session: AsyncSession,
    *,
    state_key: str,
    period_kind: str,
    period_key: str,
) -> BayesBacktestSnapshot | None:
    result = await session.execute(
        select(BayesBacktestSnapshot).where(
            BayesBacktestSnapshot.state_key == state_key,
            BayesBacktestSnapshot.period_kind == period_kind,
            BayesBacktestSnapshot.period_key == period_key,
        )
    )
    return result.scalar_one_or_none()


async def list_cached_backtest_snapshots(
    session: AsyncSession,
    *,
    state_key: str,
    period_kind: str,
    limit: int = 7,
) -> list[BayesBacktestSnapshot]:
    result = await session.execute(
        select(BayesBacktestSnapshot)
        .where(
            BayesBacktestSnapshot.state_key == state_key,
            BayesBacktestSnapshot.period_kind == period_kind,
        )
        .order_by(BayesBacktestSnapshot.generated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def save_cached_backtest_snapshot(
    session: AsyncSession,
    *,
    state_key: str,
    period_kind: str,
    period_key: str,
    rows_scored: int,
    summary_json: dict[str, Any],
) -> BayesBacktestSnapshot:
    obj = await get_cached_backtest_snapshot(
        session,
        state_key=state_key,
        period_kind=period_kind,
        period_key=period_key,
    )
    if obj is None:
        obj = BayesBacktestSnapshot(
            state_key=state_key,
            period_kind=period_kind,
            period_key=period_key,
        )
    obj.rows_scored = rows_scored
    obj.generated_at = datetime.utcnow()
    obj.summary_json = _json_safe(summary_json)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


def build_backtest_payload(
    rows: list[BacktestRow],
    *,
    confidence_thresholds: list[int] | None = None,
    expected_value_thresholds: list[float] | None = None,
    edge_thresholds: list[float] | None = None,
) -> dict[str, Any]:
    confidence_thresholds = confidence_thresholds or list(range(55, 86, 5))
    expected_value_thresholds = expected_value_thresholds or [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    edge_thresholds = edge_thresholds or [0.0]

    sweep = sweep_policies(
        rows,
        confidence_thresholds=confidence_thresholds,
        expected_value_thresholds=expected_value_thresholds,
        edge_thresholds=edge_thresholds,
    )
    baseline = score_policy(
        rows,
        min_confidence=65,
        min_expected_value=6.0,
        min_edge=0.0,
    )
    return {
        "rows_scored": len(rows),
        "baseline": baseline,
        "sweep": sweep,
    }


async def refresh_backtest_snapshots(
    session: AsyncSession,
    *,
    state_key: str,
) -> dict[str, Any]:
    rows = await load_backtest_rows(session, state_key=state_key)
    await session.execute(
        delete(BayesBacktestSnapshot).where(BayesBacktestSnapshot.state_key == state_key)
    )
    await session.commit()
    if not rows:
        empty_payload = {"rows_scored": 0, "baseline": None, "sweep": {"rows_scored": 0, "results": []}}
        await save_cached_backtest_snapshot(
            session,
            state_key=state_key,
            period_kind="all_time",
            period_key="all_time",
            rows_scored=0,
            summary_json=empty_payload,
        )
        today_key = datetime.utcnow().date().isoformat()
        await save_cached_backtest_snapshot(
            session,
            state_key=state_key,
            period_kind="daily",
            period_key=today_key,
            rows_scored=0,
            summary_json=empty_payload,
        )
        return empty_payload

    all_time_payload = build_backtest_payload(rows)
    await save_cached_backtest_snapshot(
        session,
        state_key=state_key,
        period_kind="all_time",
        period_key="all_time",
        rows_scored=all_time_payload["rows_scored"],
        summary_json=all_time_payload,
    )

    daily_groups: dict[str, list[BacktestRow]] = {}
    for row in rows:
        daily_groups.setdefault(row.bucket_date, []).append(row)

    for day_key, day_rows in daily_groups.items():
        day_payload = build_backtest_payload(day_rows)
        await save_cached_backtest_snapshot(
            session,
            state_key=state_key,
            period_kind="daily",
            period_key=day_key,
            rows_scored=day_payload["rows_scored"],
            summary_json=day_payload,
        )

    await manager.broadcast({
        "type": "backtest_snapshot_updated",
        "data": {
            "state_key": state_key,
            "rows_scored": all_time_payload["rows_scored"],
            "daily_keys": sorted(daily_groups.keys()),
            "generated_at": datetime.utcnow().isoformat(),
        },
    })

    return all_time_payload


def score_policy(
    rows: list[BacktestRow],
    *,
    min_confidence: int,
    min_expected_value: float,
    min_edge: float = 0.0,
    initial_equity: float = 1000.0,
) -> dict[str, Any]:
    accepted: list[BacktestRow] = []
    rejection_reasons: Counter[str] = Counter()

    for row in rows:
        reasons: list[str] = []
        if row.confidence < min_confidence:
            reasons.append("low_confidence")
        if row.expected_value < min_expected_value:
            reasons.append("low_expected_value")
        if row.edge < min_edge:
            reasons.append("low_edge")

        if reasons:
            for reason in reasons:
                rejection_reasons[reason] += 1
            continue
        accepted.append(row)

    wins = sum(1 for row in accepted if row.resolution == "WIN")
    losses = sum(1 for row in accepted if row.resolution == "LOSS")
    total_pnl = sum(row.pnl for row in accepted)
    avg_pnl = total_pnl / len(accepted) if accepted else 0.0
    win_rate = wins / len(accepted) if accepted else 0.0

    cumulative_pnl = 0.0
    equity_curve = [initial_equity]
    for row in accepted:
        cumulative_pnl += row.pnl
        equity_curve.append(initial_equity + cumulative_pnl)

    max_drawdown = calculate_max_drawdown(equity_curve) if len(equity_curve) > 1 else 0.0
    profit_factor = None
    gross_profit = sum(row.pnl for row in accepted if row.pnl > 0)
    gross_loss = abs(sum(row.pnl for row in accepted if row.pnl < 0))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss

    risk_adjusted_score = total_pnl - (max_drawdown * initial_equity)

    return {
        "min_confidence": min_confidence,
        "min_expected_value": min_expected_value,
        "min_edge": min_edge,
        "rows_scored": len(rows),
        "trades_taken": len(accepted),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "risk_adjusted_score": risk_adjusted_score,
        "equity_curve": equity_curve,
        "rejection_reasons": dict(rejection_reasons),
        "accepted": [_serialize_row(row) for row in accepted],
    }


def sweep_policies(
    rows: list[BacktestRow],
    *,
    confidence_thresholds: list[int],
    expected_value_thresholds: list[float],
    edge_thresholds: list[float] | None = None,
) -> dict[str, Any]:
    edge_thresholds = edge_thresholds or [0.0]
    results: list[dict[str, Any]] = []

    for conf in confidence_thresholds:
        for ev in expected_value_thresholds:
            for edge in edge_thresholds:
                results.append(
                    score_policy(
                        rows,
                        min_confidence=conf,
                        min_expected_value=ev,
                        min_edge=edge,
                    )
                )

    results.sort(
        key=lambda item: (
            item["risk_adjusted_score"],
            item["total_pnl"],
            item["win_rate"],
            -item["max_drawdown"],
            item["min_confidence"],
            item["min_expected_value"],
        ),
        reverse=True,
    )

    best_by_pnl = max(
        results,
        key=lambda item: (
            item["total_pnl"],
            item["win_rate"],
            -item["max_drawdown"],
            item["min_confidence"],
            item["min_expected_value"],
        ),
    ) if results else None
    best_by_win_rate = max(
        results,
        key=lambda item: (
            item["win_rate"],
            item["total_pnl"],
            -item["max_drawdown"],
            item["min_confidence"],
            item["min_expected_value"],
        ),
    ) if results else None
    lowest_drawdown = min(results, key=lambda item: (item["max_drawdown"], -item["total_pnl"])) if results else None

    return {
        "rows_scored": len(rows),
        "results": results,
        "best_by_risk_adjusted_score": results[0] if results else None,
        "best_by_pnl": best_by_pnl,
        "best_by_win_rate": best_by_win_rate,
        "lowest_drawdown": lowest_drawdown,
    }
