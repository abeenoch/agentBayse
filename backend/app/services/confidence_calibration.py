"""
Confidence Calibration Tracker

Tracks LLM prediction confidence vs actual outcomes to detect overconfidence
and dynamically adjust the minimum confidence threshold.

Calibration is computed from resolved signals in the database — no new
storage needed. The analysis groups historical predictions by confidence
bins and computes the actual win rate for each bin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal


CONFIDENCE_BINS = [
    (50, 59),
    (60, 69),
    (70, 79),
    (80, 89),
    (90, 100),
]


@dataclass
class CalibrationBin:
    label: str
    count: int
    wins: int
    losses: int
    actual_win_rate: float
    avg_confidence: float
    overconfidence: float  # positive = LLM is overconfident, negative = underconfident


@dataclass
class CalibrationReport:
    bins: list[CalibrationBin]
    total_predictions: int
    overall_accuracy: float
    avg_confidence: float
    calibration_error: float  # mean absolute difference between confidence and accuracy
    suggested_min_confidence: int
    is_calibrated: bool  # True if calibration_error < 0.10
async def compute_calibration(
    session: AsyncSession,
    *,
    min_samples_per_bin: int = 5,
) -> CalibrationReport:
    """
    Compute confidence calibration from resolved signals.

    Groups resolved signals by confidence bin and computes actual win rate
    per bin. Returns a CalibrationReport with calibration metrics and a
    suggested minimum confidence threshold.
    """
    result = await session.execute(
        select(Signal.confidence, Signal.resolution, Signal.signal_type)
        .where(
            Signal.resolution.in_(["WIN", "LOSS"]),
            Signal.signal_type.in_(["BUY_YES", "BUY_NO"]),
        )
    )
    rows = list(result.all())

    if not rows:
        return _empty_report()

    total = len(rows)
    total_wins = sum(1 for _, res, _ in rows if res == "WIN")
    overall_accuracy = total_wins / total if total else 0.0
    avg_confidence = sum(float(conf or 0) for conf, _, _ in rows) / total

    bins: list[CalibrationBin] = []
    for lo, hi in CONFIDENCE_BINS:
        bin_rows = [
            (conf, res)
            for conf, res, _ in rows
            if lo <= (conf or 0) <= hi
        ]
        bin_count = len(bin_rows)
        if bin_count < min_samples_per_bin:
            continue

        bin_wins = sum(1 for _, res in bin_rows if res == "WIN")
        bin_losses = bin_count - bin_wins
        actual_win_rate = bin_wins / bin_count if bin_count else 0.0
        bin_avg_conf = sum(float(conf or 0) for conf, _ in bin_rows) / bin_count
        bin_overconfidence = bin_avg_conf - (actual_win_rate * 100)

        bins.append(CalibrationBin(
            label=f"{lo}-{hi}",
            count=bin_count,
            wins=bin_wins,
            losses=bin_losses,
            actual_win_rate=actual_win_rate,
            avg_confidence=bin_avg_conf,
            overconfidence=bin_overconfidence,
        ))

    calibration_error = 0.0
    if bins:
        total_weighted_error = sum(b.count * abs(b.overconfidence) for b in bins)
        total_samples = sum(b.count for b in bins)
        calibration_error = total_weighted_error / (total_samples * 100) if total_samples else 0.0

    suggested_min = 50
    if bins:
        for b in bins:
            if b.actual_win_rate >= 0.55 and b.count >= min_samples_per_bin:
                label_parts = b.label.split("-")
                if label_parts:
                    suggested_min = max(suggested_min, int(label_parts[0]))
                break
        lowest_bin = bins[0] if bins else None
        if lowest_bin and lowest_bin.overconfidence > 5:
            suggested_min = min(suggested_min + int(lowest_bin.overconfidence / 2), 85)

    return CalibrationReport(
        bins=bins,
        total_predictions=total,
        overall_accuracy=overall_accuracy,
        avg_confidence=avg_confidence,
        calibration_error=calibration_error,
        suggested_min_confidence=suggested_min,
        is_calibrated=calibration_error < 0.10,
    )


def _empty_report() -> CalibrationReport:
    return CalibrationReport(
        bins=[],
        total_predictions=0,
        overall_accuracy=0.0,
        avg_confidence=0.0,
        calibration_error=0.0,
        suggested_min_confidence=50,
        is_calibrated=False,
    )


def calibration_to_dict(report: CalibrationReport) -> dict[str, Any]:
    """Convert a CalibrationReport to a JSON-serializable dict."""
    return {
        "bins": [
            {
                "label": b.label,
                "count": b.count,
                "wins": b.wins,
                "losses": b.losses,
                "actual_win_rate": round(b.actual_win_rate, 3),
                "avg_confidence": round(b.avg_confidence, 1),
                "overconfidence": round(b.overconfidence, 1),
            }
            for b in report.bins
        ],
        "total_predictions": report.total_predictions,
        "overall_accuracy": round(report.overall_accuracy, 3),
        "avg_confidence": round(report.avg_confidence, 1),
        "calibration_error": round(report.calibration_error, 3),
        "suggested_min_confidence": report.suggested_min_confidence,
        "is_calibrated": report.is_calibrated,
    }