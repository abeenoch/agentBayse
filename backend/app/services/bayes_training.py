from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp, log, tanh
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bayes_training_run import BayesTrainingRun
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.bayes_backtest import (
    BacktestRow,
    load_backtest_rows,
    load_market_snapshot_index,
    _is_win_for_signal,
    score_policy,
    select_market_snapshot,
    snapshot_context_features,
)
from app.utils.logger import logger
from app.websocket_manager import manager


FEATURE_NAMES = [
    "bias",
    "is_buy_no",
    "confidence",
    "estimated_probability",
    "market_price",
    "expected_value",
    "rank_score",
    "market_liquidity",
    "market_volume",
    "market_orders",
    "market_spread",
    "market_imbalance",
    "snapshot_age_seconds",
]

FEATURE_GROUPS = {
    "market_context": {"market_liquidity", "market_volume", "market_orders", "market_spread", "market_imbalance", "snapshot_age_seconds"},
    "price_and_prob": {"confidence", "estimated_probability", "market_price", "expected_value", "rank_score"},
    "side_indicator": {"is_buy_no"},
    "bias": {"bias"},
}


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


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


def _normalize_price(value: float | None) -> float:
    if value is None:
        return 0.0
    price = float(value)
    if price > 1.0:
        price /= 100.0
    return max(0.0, min(1.0, price))


def _scale_ev(value: float | None) -> float:
    return tanh(float(value or 0.0) / 100.0)


def _scale_rank(value: float | None) -> float:
    return tanh(float(value or 0.0) / 100.0)


@dataclass
class TrainingRow:
    created_at: datetime
    signal_type: str
    label: int
    state_key: str
    features: list[float]


def _build_features(signal: Signal, trade: Trade | None = None, snapshot_features: dict[str, float] | None = None) -> list[float]:
    prob = float(signal.estimated_probability or 0.0)
    price = _normalize_price(signal.market_price_at_signal)
    conf = _clip(float(signal.confidence or 0.0) / 100.0, 0.0, 1.0)
    is_buy_no = 1.0 if (signal.signal_type or "").upper() == "BUY_NO" else 0.0
    as_of = signal.created_at or (trade.created_at if trade else None) or datetime.utcnow()
    snapshot_features = snapshot_features or snapshot_context_features(None, as_of=as_of)
    return [
        1.0,
        is_buy_no,
        conf,
        prob,
        price,
        _scale_ev(signal.expected_value),
        _scale_rank(signal.rank_score),
        snapshot_features["market_liquidity"],
        snapshot_features["market_volume"],
        snapshot_features["market_orders"],
        snapshot_features["market_spread"],
        snapshot_features["market_imbalance"],
        snapshot_features["snapshot_age_seconds"],
    ]


def _build_backtest_features(row: BacktestRow) -> list[float]:
    prob = float(row.estimated_probability or 0.0)
    price = _normalize_price(row.market_price_at_signal)
    conf = _clip(float(row.confidence or 0.0) / 100.0, 0.0, 1.0)
    is_buy_no = 1.0 if (row.signal_type or "").upper() == "BUY_NO" else 0.0
    return [
        1.0,
        is_buy_no,
        conf,
        prob,
        price,
        _scale_ev(row.expected_value),
        _scale_rank(row.rank_score),
        float(row.market_liquidity or 0.0),
        float(row.market_volume or 0.0),
        float(row.market_orders or 0.0),
        float(row.market_spread or 0.0),
        float(row.market_imbalance or 0.0),
        float(row.snapshot_age_seconds or 0.0),
    ]


async def load_training_rows(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> list[TrainingRow]:
    query = (
        select(Signal, Trade)
        .outerjoin(Trade, Trade.signal_id == Signal.id)
        .where(
            Signal.signal_type.in_(["BUY_YES", "BUY_NO"]),
            Signal.resolution.in_(["WIN", "LOSS"]),
        )
        .order_by(Signal.created_at.asc())
    )
    if state_key:
        query = query.where(func.coalesce(Trade.bayes_state_key, Signal.bayes_state_key) == state_key)

    result = await session.execute(query)
    raw_rows = list(result.all())
    market_ids = {signal.market_id for signal, _ in raw_rows if signal.market_id}
    snapshot_index = await load_market_snapshot_index(session, market_ids=market_ids)

    rows: list[TrainingRow] = []
    for signal, trade in raw_rows:
        outcome = str(trade.resolution if trade else signal.resolution or "").strip().upper()
        if outcome not in {"WIN", "LOSS"}:
            continue
        as_of = signal.created_at or (trade.created_at if trade else None) or datetime.utcnow()
        snapshot = select_market_snapshot(snapshot_index, market_id=signal.market_id, as_of=as_of)
        snapshot_features = snapshot_context_features(snapshot, as_of=as_of)
        state_key = getattr(trade, "bayes_state_key", None) if trade else None
        state_key = state_key or getattr(signal, "bayes_state_key", None) or "default"
        rows.append(
            TrainingRow(
                created_at=as_of,
                signal_type=signal.signal_type,
                label=1 if _is_win_for_signal(signal.signal_type, outcome) else 0,
                state_key=state_key,
                features=_build_features(signal, trade, snapshot_features),
            )
        )
    return rows


def _split_rows(rows: list[TrainingRow], train_ratio: float = 0.8) -> tuple[list[TrainingRow], list[TrainingRow]]:
    if len(rows) <= 1:
        return rows, []
    cutoff = max(1, min(len(rows) - 1, int(round(len(rows) * train_ratio))))
    return rows[:cutoff], rows[cutoff:]


def _standardize(matrix: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    if not matrix:
        return [], [], []
    feature_count = len(matrix[0])
    means = [0.0] * feature_count
    stds = [1.0] * feature_count

    for col in range(feature_count):
        if col == 0:
            # Preserve the bias term as a true intercept feature.
            means[col] = 0.0
            stds[col] = 1.0
            continue
        values = [row[col] for row in matrix]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = variance ** 0.5 or 1.0
        means[col] = mean
        stds[col] = std

    normalized = [
        [
            row[col] if col == 0 else (row[col] - means[col]) / stds[col]
            for col in range(feature_count)
        ]
        for row in matrix
    ]
    return normalized, means, stds


def _apply_standardize(matrix: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    if not matrix:
        return []
    return [
        [
            row[col] if col == 0 else (row[col] - means[col]) / (stds[col] or 1.0)
            for col in range(len(means))
        ]
        for row in matrix
    ]


def _feature_indices(feature_names: list[str]) -> dict[str, int]:
    return {name: idx for idx, name in enumerate(feature_names)}


def _select_columns(matrix: list[list[float]], columns: list[int]) -> list[list[float]]:
    if not matrix:
        return []
    return [[row[idx] for idx in columns] for row in matrix]


def _train_logistic_regression(
    matrix: list[list[float]],
    labels: list[int],
    *,
    learning_rate: float = 0.15,
    epochs: int = 400,
    l2: float = 0.01,
) -> list[float]:
    if not matrix:
        return [0.0]
    weights = [0.0] * len(matrix[0])
    for _ in range(epochs):
        grads = [0.0] * len(weights)
        for row, label in zip(matrix, labels):
            z = sum(w * x for w, x in zip(weights, row))
            pred = _sigmoid(z)
            error = pred - label
            for idx, value in enumerate(row):
                grads[idx] += error * value
        n = max(len(matrix), 1)
        for idx in range(len(weights)):
            grads[idx] = grads[idx] / n + l2 * weights[idx]
            weights[idx] -= learning_rate * grads[idx]
    return weights


def _predict(matrix: list[list[float]], weights: list[float]) -> list[float]:
    return [_sigmoid(sum(w * x for w, x in zip(weights, row))) for row in matrix]


def _fit_logistic_model(
    matrix: list[list[float]],
    labels: list[int],
    *,
    learning_rate: float = 0.12,
    epochs: int = 500,
    validation_ratio: float = 0.2,
    candidate_l2s: tuple[float, ...] = (0.001, 0.003, 0.01, 0.03, 0.08),
) -> tuple[list[float], float]:
    if not matrix:
        return [0.0], 0.0

    if len(matrix) < 4:
        return _train_logistic_regression(matrix, labels, learning_rate=learning_rate, epochs=epochs), candidate_l2s[0]

    val_size = max(1, min(len(matrix) - 1, int(round(len(matrix) * validation_ratio))))
    train_matrix = matrix[:-val_size]
    train_labels = labels[:-val_size]
    val_matrix = matrix[-val_size:]
    val_labels = labels[-val_size:]

    best_l2 = candidate_l2s[0]
    best_score: tuple[float, float] | None = None
    for l2 in candidate_l2s:
        weights = _train_logistic_regression(
            train_matrix,
            train_labels,
            learning_rate=learning_rate,
            epochs=epochs,
            l2=l2,
        )
        val_probs = _predict(val_matrix, weights)
        metrics = _binary_metrics(val_labels, val_probs)
        score = (metrics["log_loss"], metrics["brier"])
        if best_score is None or score < best_score:
            best_score = score
            best_l2 = l2

    return (
        _train_logistic_regression(
            matrix,
            labels,
            learning_rate=learning_rate,
            epochs=epochs,
            l2=best_l2,
        ),
        best_l2,
    )


def _binary_metrics(labels: list[int], probs: list[float]) -> dict[str, float]:
    if not labels:
        return {"brier": 0.0, "log_loss": 0.0, "accuracy": 0.0}
    eps = 1e-6
    brier = sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(labels)
    log_loss = -sum(y * log(max(p, eps)) + (1 - y) * log(max(1 - p, eps)) for p, y in zip(probs, labels)) / len(labels)
    accuracy = sum(1 for p, y in zip(probs, labels) if (p >= 0.5) == bool(y)) / len(labels)
    return {"brier": brier, "log_loss": log_loss, "accuracy": accuracy}


def _policy_from_probs(
    rows: list[BacktestRow],
    probs: list[float],
    *,
    min_probability_edge: float,
) -> dict[str, Any]:
    accepted = []
    rejection_reasons: dict[str, int] = {}

    for row, prob in zip(rows, probs):
        edge = prob - _normalize_price(row.market_price_at_signal)
        if edge < min_probability_edge:
            rejection_reasons["low_edge"] = rejection_reasons.get("low_edge", 0) + 1
            continue
        accepted.append(
            {
                "resolution": row.resolution,
                "pnl": row.pnl,
            }
        )

    wins = sum(1 for row in accepted if row["resolution"] == "WIN")
    losses = sum(1 for row in accepted if row["resolution"] == "LOSS")
    total_pnl = sum(float(row["pnl"] or 0.0) for row in accepted)
    win_rate = wins / len(accepted) if accepted else 0.0
    return {
        "trades_taken": len(accepted),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "rejection_reasons": rejection_reasons,
    }


def _calibration_bins(labels: list[int], probs: list[float], *, bins: int = 10) -> list[dict[str, Any]]:
    if not labels:
        return []
    buckets: dict[int, list[tuple[float, int]]] = {}
    for prob, label in zip(probs, labels):
        bucket = min(bins - 1, max(0, int(prob * bins)))
        buckets.setdefault(bucket, []).append((prob, label))
    result = []
    for bucket in range(bins):
        items = buckets.get(bucket, [])
        if not items:
            continue
        avg_pred = sum(prob for prob, _ in items) / len(items)
        avg_obs = sum(label for _, label in items) / len(items)
        result.append(
            {
                "bin": bucket,
                "range_start": bucket / bins,
                "range_end": (bucket + 1) / bins,
                "count": len(items),
                "avg_pred": avg_pred,
                "observed": avg_obs,
                "gap": abs(avg_pred - avg_obs),
            }
        )
    return result


def _side_calibration(rows: list[TrainingRow], probs: list[float], *, side: str) -> dict[str, Any]:
    side_rows = [(row, prob) for row, prob in zip(rows, probs) if row.signal_type == side]
    labels = [row.label for row, _ in side_rows]
    side_probs = [prob for _, prob in side_rows]
    return {
        "count": len(side_rows),
        "metrics": _binary_metrics(labels, side_probs),
        "bins": _calibration_bins(labels, side_probs),
    }


async def build_calibration_audit(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> dict[str, Any]:
    rows = await load_training_rows(session, state_key=state_key)
    if not rows:
        return {
            "state_key": state_key or "default",
            "sample_size": 0,
            "positive_rate": 0.0,
            "overall": {"metrics": _binary_metrics([], []), "bins": []},
            "yes": {"count": 0, "metrics": _binary_metrics([], []), "bins": []},
            "no": {"count": 0, "metrics": _binary_metrics([], []), "bins": []},
        }

    labels = [row.label for row in rows]
    matrix = [row.features for row in rows]
    standardized, means, stds = _standardize(matrix)
    weights, _ = _fit_logistic_model(standardized, labels, epochs=250)
    probs = _predict(standardized, weights)

    return {
        "state_key": state_key or "default",
        "sample_size": len(rows),
        "positive_rate": sum(labels) / len(labels),
        "overall": {
            "metrics": _binary_metrics(labels, probs),
            "bins": _calibration_bins(labels, probs),
        },
        "yes": _side_calibration(rows, probs, side="BUY_YES"),
        "no": _side_calibration(rows, probs, side="BUY_NO"),
    }


async def train_bayes_model(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> dict[str, Any]:
    rows = await load_training_rows(session, state_key=state_key)
    if not rows:
        return {
            "state_key": state_key or "default",
            "model_version": "logreg_v1",
            "sample_size": 0,
            "train_size": 0,
            "test_size": 0,
            "positive_rate": 0.0,
            "feature_names": FEATURE_NAMES,
            "coefficients": {"bias": 0.0, "weights": [0.0] * (len(FEATURE_NAMES) - 1), "means": [], "stds": []},
            "metrics": {"train": _binary_metrics([], []), "test": _binary_metrics([], [])},
            "calibration": {"overall": {"metrics": _binary_metrics([], []), "bins": []}, "yes": {"count": 0, "metrics": _binary_metrics([], []), "bins": []}, "no": {"count": 0, "metrics": _binary_metrics([], []), "bins": []}},
        }

    train_rows, test_rows = _split_rows(rows)
    train_labels = [row.label for row in train_rows]
    test_labels = [row.label for row in test_rows]
    train_matrix = [row.features for row in train_rows]
    test_matrix = [row.features for row in test_rows]
    standardized_train, means, stds = _standardize(train_matrix)
    standardized_test = _apply_standardize(test_matrix, means, stds)

    weights, chosen_l2 = _fit_logistic_model(standardized_train, train_labels)
    train_probs = _predict(standardized_train, weights)
    test_probs = _predict(standardized_test, weights)

    if weights:
        bias = weights[0]
        coef = weights[1:]
    else:
        bias = 0.0
        coef = []

    calibration = {
        "overall": {
            "metrics": _binary_metrics(test_labels, test_probs),
            "bins": _calibration_bins(test_labels, test_probs),
        },
        "yes": _side_calibration(test_rows, test_probs, side="BUY_YES"),
        "no": _side_calibration(test_rows, test_probs, side="BUY_NO"),
    }

    metrics = {
        "train": _binary_metrics(train_labels, train_probs),
        "test": _binary_metrics(test_labels, test_probs),
    }

    artifact = {
        "state_key": state_key or "default",
        "model_version": "logreg_v1",
        "sample_size": len(rows),
        "train_size": len(train_rows),
        "test_size": len(test_rows),
        "positive_rate": sum(row.label for row in rows) / len(rows),
        "feature_names": FEATURE_NAMES,
        "coefficients": {
            "bias": bias,
            "weights": coef,
            "means": means,
            "stds": stds,
            "chosen_l2": chosen_l2,
        },
        "metrics": metrics,
        "calibration": calibration,
    }

    run = BayesTrainingRun(
        state_key=artifact["state_key"],
        model_version=artifact["model_version"],
        sample_size=artifact["sample_size"],
        train_size=artifact["train_size"],
        test_size=artifact["test_size"],
        positive_rate=artifact["positive_rate"],
        feature_names=artifact["feature_names"],
        coefficients=_json_safe(artifact["coefficients"]),
        metrics_json=_json_safe(artifact["metrics"]),
        calibration_json=_json_safe(artifact["calibration"]),
        trained_at=datetime.utcnow(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        await manager.broadcast(
            {
                "type": "bayes_training_run",
                "data": {
                    "id": str(run.id),
                    "state_key": run.state_key,
                    "model_version": run.model_version,
                    "sample_size": run.sample_size,
                    "train_size": run.train_size,
                    "test_size": run.test_size,
                    "positive_rate": run.positive_rate,
                    "trained_at": run.trained_at.isoformat() if run.trained_at else None,
                },
            }
        )
    except Exception:
        pass

    logger.info(
        "Bayes training run saved: state_key=%s sample=%d train=%d test=%d",
        run.state_key,
        run.sample_size,
        run.train_size,
        run.test_size,
    )

    return {
        **artifact,
        "trained_at": run.trained_at.isoformat() if run.trained_at else None,
        "run_id": str(run.id),
    }


def _walk_forward_splits(rows: list[BacktestRow], *, min_train_size: int = 40, test_size: int = 20, step_size: int = 20) -> list[tuple[list[BacktestRow], list[BacktestRow]]]:
    if len(rows) < min_train_size + 1:
        return []
    splits: list[tuple[list[BacktestRow], list[BacktestRow]]] = []
    train_end = min_train_size
    while train_end < len(rows):
        test_end = min(train_end + test_size, len(rows))
        train_rows = rows[:train_end]
        test_rows = rows[train_end:test_end]
        if not test_rows:
            break
        splits.append((train_rows, test_rows))
        if test_end >= len(rows):
            break
        train_end += max(1, step_size)
    return splits


async def build_offline_eval_report(
    session: AsyncSession,
    *,
    state_key: str | None = None,
    min_train_size: int = 40,
    test_size: int = 20,
    step_size: int = 20,
    policy_edge_threshold: float = 0.04,
) -> dict[str, Any]:
    rows = await load_backtest_rows(session, state_key=state_key)
    rows = sorted(rows, key=lambda row: row.created_at)
    splits = _walk_forward_splits(rows, min_train_size=min_train_size, test_size=test_size, step_size=step_size)

    folds: list[dict[str, Any]] = []
    all_test_labels: list[int] = []
    all_test_probs: list[float] = []
    all_model_policy_rows: list[BacktestRow] = []
    all_model_policy_probs: list[float] = []
    baseline_rows: list[BacktestRow] = []

    for fold_index, (train_rows, test_rows) in enumerate(splits, start=1):
        train_labels = [1 if _is_win_for_signal(row.signal_type, row.resolution) else 0 for row in train_rows]
        test_labels = [1 if _is_win_for_signal(row.signal_type, row.resolution) else 0 for row in test_rows]
        train_matrix = [_build_backtest_features(row) for row in train_rows]
        test_matrix = [_build_backtest_features(row) for row in test_rows]
        standardized_train, means, stds = _standardize(train_matrix)
        standardized_test = _apply_standardize(test_matrix, means, stds)
        weights, _ = _fit_logistic_model(standardized_train, train_labels, epochs=250)
        train_probs = _predict(standardized_train, weights)
        test_probs = _predict(standardized_test, weights)

        model_metrics = _binary_metrics(test_labels, test_probs)
        calibration = _calibration_bins(test_labels, test_probs)
        model_policy = _policy_from_probs(test_rows, test_probs, min_probability_edge=policy_edge_threshold)
        baseline_policy = score_policy(test_rows, min_confidence=65, min_expected_value=6.0, min_edge=0.0)

        folds.append(
            {
                "fold": fold_index,
                "train_size": len(train_rows),
                "test_size": len(test_rows),
                "train_metrics": _binary_metrics(train_labels, train_probs),
                "test_metrics": model_metrics,
                "calibration": calibration,
                "model_policy": model_policy,
                "baseline_policy": baseline_policy,
                "test_start": test_rows[0].created_at.isoformat() if test_rows else None,
                "test_end": test_rows[-1].created_at.isoformat() if test_rows else None,
            }
        )

        all_test_labels.extend(test_labels)
        all_test_probs.extend(test_probs)
        all_model_policy_rows.extend(test_rows)
        all_model_policy_probs.extend(test_probs)
        baseline_rows.extend(test_rows)

    overall_metrics = _binary_metrics(all_test_labels, all_test_probs)
    overall_calibration = _calibration_bins(all_test_labels, all_test_probs)
    aggregated_model_policy = _policy_from_probs(all_model_policy_rows, all_model_policy_probs, min_probability_edge=policy_edge_threshold)
    aggregated_baseline = score_policy(baseline_rows, min_confidence=65, min_expected_value=6.0, min_edge=0.0)

    return {
        "state_key": state_key or "default",
        "sample_size": len(rows),
        "fold_count": len(folds),
        "split_config": {
            "min_train_size": min_train_size,
            "test_size": test_size,
            "step_size": step_size,
            "policy_edge_threshold": policy_edge_threshold,
        },
        "overall_metrics": overall_metrics,
        "overall_calibration": overall_calibration,
        "walk_forward": folds,
        "model_policy": aggregated_model_policy,
        "baseline_policy": aggregated_baseline,
        "label_rate": (sum(1 for row in rows if row.resolution == "WIN") / len(rows)) if rows else 0.0,
    }


async def build_feature_ablation_report(
    session: AsyncSession,
    *,
    state_key: str | None = None,
    min_train_size: int = 40,
    test_size: int = 20,
    step_size: int = 20,
    policy_edge_threshold: float = 0.04,
) -> dict[str, Any]:
    rows = await load_backtest_rows(session, state_key=state_key)
    rows = sorted(rows, key=lambda row: row.created_at)
    splits = _walk_forward_splits(rows, min_train_size=min_train_size, test_size=test_size, step_size=step_size)
    if not splits:
        return {
            "state_key": state_key or "default",
            "sample_size": len(rows),
            "feature_names": FEATURE_NAMES,
            "baseline": None,
            "ablations": [],
        }

    feature_index = _feature_indices(FEATURE_NAMES)
    ablation_rows: list[dict[str, Any]] = []

    def evaluate(columns: list[int]) -> dict[str, Any]:
        all_labels: list[int] = []
        all_probs: list[float] = []
        policy_rows: list[BacktestRow] = []
        policy_probs: list[float] = []
        for train_rows, test_rows in splits:
            train_labels = [1 if _is_win_for_signal(row.signal_type, row.resolution) else 0 for row in train_rows]
            test_labels = [1 if _is_win_for_signal(row.signal_type, row.resolution) else 0 for row in test_rows]
            train_matrix = _select_columns([_build_backtest_features(row) for row in train_rows], columns)
            test_matrix = _select_columns([_build_backtest_features(row) for row in test_rows], columns)
            standardized_train, means, stds = _standardize(train_matrix)
            standardized_test = _apply_standardize(test_matrix, means, stds)
            weights, _ = _fit_logistic_model(standardized_train, train_labels, epochs=250)
            test_probs = _predict(standardized_test, weights)
            all_labels.extend(test_labels)
            all_probs.extend(test_probs)
            policy_rows.extend(test_rows)
            policy_probs.extend(test_probs)
        metrics = _binary_metrics(all_labels, all_probs)
        policy = _policy_from_probs(policy_rows, policy_probs, min_probability_edge=policy_edge_threshold)
        return {
            "metrics": metrics,
            "policy": policy,
            "selected_features": [FEATURE_NAMES[idx] for idx in columns],
        }

    full_columns = list(range(len(FEATURE_NAMES)))
    baseline = evaluate(full_columns)
    ablation_rows.append(
        {
            "name": "all_features",
            "removed_features": [],
            **baseline,
        }
    )

    for feature_name in FEATURE_NAMES:
        if feature_name == "bias":
            continue
        columns = [idx for idx, name in enumerate(FEATURE_NAMES) if name != feature_name]
        result = evaluate(columns)
        ablation_rows.append(
            {
                "name": f"minus_{feature_name}",
                "removed_features": [feature_name],
                **result,
            }
        )

    for group_name, group_features in FEATURE_GROUPS.items():
        columns = [idx for idx, name in enumerate(FEATURE_NAMES) if name not in group_features]
        result = evaluate(columns)
        ablation_rows.append(
            {
                "name": f"minus_group_{group_name}",
                "removed_features": sorted(group_features),
                **result,
            }
        )

    return {
        "state_key": state_key or "default",
        "sample_size": len(rows),
        "fold_count": len(splits),
        "split_config": {
            "min_train_size": min_train_size,
            "test_size": test_size,
            "step_size": step_size,
            "policy_edge_threshold": policy_edge_threshold,
        },
        "baseline": baseline,
        "ablations": ablation_rows,
    }


async def get_latest_training_run(
    session: AsyncSession,
    *,
    state_key: str | None = None,
) -> BayesTrainingRun | None:
    query = select(BayesTrainingRun).order_by(BayesTrainingRun.trained_at.desc())
    if state_key:
        query = query.where(BayesTrainingRun.state_key == state_key)
    result = await session.execute(query.limit(1))
    return result.scalars().first()


async def resolve_live_training_run(
    session: AsyncSession,
    *,
    state_key: str | None = None,
    default_key: str = "default",
) -> tuple[BayesTrainingRun | None, str]:
    """
    Resolve the best available training run for live trading.

    Preference order:
      1. The requested state key.
      2. The default state key.
      3. The latest training run in the database.
    """
    candidate_keys: list[str | None] = []
    if state_key:
        candidate_keys.append(state_key)
    if default_key and default_key not in candidate_keys:
        candidate_keys.append(default_key)
    candidate_keys.append(None)

    seen: set[str | None] = set()
    for candidate_key in candidate_keys:
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        run = await get_latest_training_run(session, state_key=candidate_key)
        if run is not None:
            resolved_key = candidate_key or run.state_key
            return run, resolved_key

    return None, state_key or default_key
