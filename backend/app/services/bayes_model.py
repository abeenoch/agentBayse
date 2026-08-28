from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp, log, tanh
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.services.feature_encoder import FeatureEncoding


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def _logit(probability: float) -> float:
    p = _clip(probability, 1e-6, 1 - 1e-6)
    return log(p / (1.0 - p))


def _vector_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


class BayesState(BaseModel):
    model_version: str = "v1"
    alpha: float = Field(default=1.0, ge=0.0)
    beta: float = Field(default=1.0, ge=0.0)
    yes_updates: int = Field(default=0, ge=0)
    no_updates: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def prior_yes(self) -> float:
        total = self.alpha + self.beta
        return self.alpha / total if total else 0.5


class BayesPosterior(BaseModel):
    model_version: str = "v1"
    posterior_yes: float = Field(ge=0.0, le=1.0)
    posterior_no: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    suggested_action: str
    model_confidence: float = Field(ge=0.0, le=1.0)
    evidence_score: float
    prior_yes: float
    breakdown: dict[str, float] = Field(default_factory=dict)


@dataclass
class BayesDecisionContext:
    yes_price: float | None = None
    no_price: float | None = None
    open_positions: int = 0
    max_open_positions: int = 0


TRAINING_FEATURE_NAMES = [
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


def _scale_liquidity(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 10000.0) / 10000.0)


def _scale_volume(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 50000.0) / 50000.0)


def _scale_orders(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 250.0) / 250.0)


def _scale_age_seconds(value: float | None) -> float:
    return _normalize_price(min(float(value or 0.0), 86400.0) / 86400.0)


@dataclass
class BayesPolicyInput:
    signal_type: str
    confidence: float
    estimated_probability: float
    market_price: float
    expected_value: float
    rank_score: float | None
    market_liquidity: float | None = None
    market_volume: float | None = None
    market_orders: float | None = None
    market_spread: float | None = None
    market_imbalance: float | None = None
    snapshot_age_seconds: float | None = None

    def to_vector(self) -> list[float]:
        return [
            1.0,
            1.0 if (self.signal_type or "").strip().upper() == "BUY_NO" else 0.0,
            _clip(float(self.confidence or 0.0) / 100.0, 0.0, 1.0),
            _clip(float(self.estimated_probability or 0.0), 0.0, 1.0),
            _normalize_price(self.market_price),
            _scale_ev(self.expected_value),
            _scale_rank(self.rank_score),
            _scale_liquidity(self.market_liquidity),
            _scale_volume(self.market_volume),
            _scale_orders(self.market_orders),
            _normalize_price(abs(float(self.market_spread or 0.0))),
            max(-1.0, min(1.0, float(self.market_imbalance or 0.0))),
            _scale_age_seconds(self.snapshot_age_seconds),
        ]


@dataclass
class BayesTrainingArtifact:
    model_version: str
    feature_names: list[str]
    bias: float
    weights: list[float]
    means: list[float]
    stds: list[float]

    @classmethod
    def from_training_run(cls, run: Any) -> "BayesTrainingArtifact":
        coefficients = run.coefficients or {}
        weights = [float(value) for value in (coefficients.get("weights") or [])]
        means = [float(value) for value in (coefficients.get("means") or [])]
        stds = [float(value) for value in (coefficients.get("stds") or [])]
        feature_names = list(run.feature_names or TRAINING_FEATURE_NAMES)
        return cls(
            model_version=str(run.model_version or "logreg_v1"),
            feature_names=feature_names,
            bias=float(coefficients.get("bias", 0.0) or 0.0),
            weights=weights,
            means=means,
            stds=stds,
        )

    def is_usable(self) -> bool:
        return bool(self.weights) and len(self.weights) == max(len(self.feature_names) - 1, 0)

    def score(self, features: list[float]) -> float:
        if not self.is_usable():
            return 0.5
        if not features:
            return 0.5
        normalized: list[float] = []
        for idx, value in enumerate(features):
            if idx == 0:
                normalized.append(1.0)
                continue
            mean = self.means[idx] if idx < len(self.means) else 0.0
            std = self.stds[idx] if idx < len(self.stds) else 1.0
            normalized.append((float(value) - mean) / (std or 1.0))
        z = self.bias
        for weight, value in zip(self.weights, normalized[1:]):
            z += weight * value
        return _sigmoid(z)


class BayesLinearPolicy:
    def __init__(self, artifact: BayesTrainingArtifact):
        self.artifact = artifact

    @classmethod
    def from_training_run(cls, run: Any) -> "BayesLinearPolicy" | None:
        artifact = BayesTrainingArtifact.from_training_run(run)
        if not artifact.is_usable():
            return None
        # Minimum-sample gate: a model trained on a handful of resolved trades is
        # statistically meaningless and would override the live Bayesian posterior
        # with memorized noise. Keep it as a dashboard artifact until it has seen
        # enough outcomes to be trustworthy.
        sample_size = int(getattr(run, "sample_size", 0) or 0)
        if sample_size < settings.agent_min_train_samples:
            return None
        return cls(artifact)

    def score_candidate(
        self,
        candidate: BayesPolicyInput,
        context: BayesDecisionContext | None = None,
    ) -> BayesPosterior:
        context = context or BayesDecisionContext()
        score = self.artifact.score(candidate.to_vector())
        if (candidate.signal_type or "").strip().upper() == "BUY_NO":
            posterior_yes = 1.0 - score
            posterior_no = score
        else:
            posterior_yes = score
            posterior_no = 1.0 - score

        uncertainty = _clip(1.0 - abs(posterior_yes - posterior_no), 0.0, 1.0)
        model_confidence = _clip(
            (1.0 - uncertainty) * 0.75 + _clip(float(candidate.confidence or 0.0) / 100.0, 0.0, 1.0) * 0.25,
            0.0,
            1.0,
        )
        suggested_action = self._suggested_action(posterior_yes, posterior_no, context, uncertainty)
        return BayesPosterior(
            model_version=self.artifact.model_version,
            posterior_yes=posterior_yes,
            posterior_no=posterior_no,
            uncertainty=uncertainty,
            suggested_action=suggested_action,
            model_confidence=model_confidence,
            evidence_score=score,
            prior_yes=posterior_yes,
            breakdown={
                "trained_probability": score,
            },
        )

    def _suggested_action(
        self,
        posterior_yes: float,
        posterior_no: float,
        context: BayesDecisionContext,
        uncertainty: float,
    ) -> str:
        if context.max_open_positions and context.open_positions >= context.max_open_positions:
            return "AVOID"
        if uncertainty >= 0.75:
            return "AVOID"

        yes_edge = None if context.yes_price is None else posterior_yes - context.yes_price
        no_edge = None if context.no_price is None else posterior_no - context.no_price

        if yes_edge is not None and yes_edge >= 0.12 and posterior_yes >= 0.68:
            return "BUY_YES"
        if no_edge is not None and no_edge >= 0.12 and posterior_no >= 0.68:
            return "BUY_NO"
        if posterior_yes >= 0.66 and (yes_edge is None or yes_edge >= 0.04):
            return "BUY_YES"
        if posterior_no >= 0.66 and (no_edge is None or no_edge >= 0.04):
            return "BUY_NO"
        return "HOLD"


class BayesModel:
    def __init__(self, state: BayesState | None = None):
        self.state = state or BayesState()

    @classmethod
    def from_counts(
        cls,
        *,
        alpha: float = 1.0,
        beta: float = 1.0,
        yes_updates: int = 0,
        no_updates: int = 0,
        model_version: str = "v1",
    ) -> "BayesModel":
        state = BayesState(
            model_version=model_version,
            alpha=alpha,
            beta=beta,
            yes_updates=yes_updates,
            no_updates=no_updates,
        )
        return cls(state=state)

    def infer(self, features: FeatureEncoding, context: BayesDecisionContext | None = None) -> BayesPosterior:
        context = context or BayesDecisionContext()
        prior_yes = self._prior_for_event(features)

        text_signal = self._score_text(features)
        market_signal = self._score_market(features)
        portfolio_signal = self._score_portfolio(features)
        cross_signal = self._score_cross_market(features)
        time_signal = self._score_time(features)

        evidence_score = (
            1.25 * text_signal
            + 1.10 * market_signal
            - 0.85 * portfolio_signal
            - 0.70 * cross_signal
            + 0.55 * time_signal
            + 0.35 * (features.sentiment_polarity or 0.0)
            - 0.60 * (features.uncertainty or 0.0)
            + 0.30 * (features.encoder_confidence or 0.0)
            + 0.20 * (features.resolution_clarity or 0.0)
        )

        posterior_yes = _sigmoid(_logit(prior_yes) + evidence_score)
        posterior_no = 1.0 - posterior_yes
        uncertainty = self._uncertainty(features, posterior_yes, posterior_no)
        model_confidence = _clip((1.0 - uncertainty) * 0.75 + features.encoder_confidence * 0.25, 0.0, 1.0)
        suggested_action = self._suggested_action(posterior_yes, posterior_no, context, uncertainty)

        return BayesPosterior(
            posterior_yes=posterior_yes,
            posterior_no=posterior_no,
            uncertainty=uncertainty,
            suggested_action=suggested_action,
            model_confidence=model_confidence,
            evidence_score=evidence_score,
            prior_yes=prior_yes,
            breakdown={
                "text_signal": text_signal,
                "market_signal": market_signal,
                "portfolio_signal": portfolio_signal,
                "cross_signal": cross_signal,
                "time_signal": time_signal,
            },
        )

    def update_from_resolution(self, resolved_yes: bool, weight: float = 1.0) -> BayesState:
        weight = _clip(weight, 0.1, 5.0)
        if resolved_yes:
            self.state.alpha += weight
            self.state.yes_updates += 1
        else:
            self.state.beta += weight
            self.state.no_updates += 1
        self.state.updated_at = datetime.utcnow()
        return self.state

    def _prior_for_event(self, features: FeatureEncoding) -> float:
        base = self.state.prior_yes
        event_type = (features.event_type or "").lower()
        if event_type in {"crypto", "market", "fx"}:
            base += 0.02
        elif event_type in {"politics", "sports"}:
            base -= 0.01
        return _clip(base, 0.05, 0.95)

    def _score_text(self, features: FeatureEncoding) -> float:
        return _clip(
            0.55 * features.semantic_strength()
            + 0.25 * features.narrative_strength
            + 0.20 * (1.0 - features.uncertainty)
            + 0.15 * features.sentiment_polarity,
            -1.0,
            1.0,
        )

    def _score_market(self, features: FeatureEncoding) -> float:
        return _clip(
            0.50 * features.market_strength()
            + 0.20 * features.news_relevance
            + 0.20 * (1.0 - features.time_sensitivity)
            + 0.10 * features.resolution_clarity,
            -1.0,
            1.0,
        )

    def _score_portfolio(self, features: FeatureEncoding) -> float:
        return _clip(
            0.65 * features.portfolio_pressure()
            + 0.20 * max(features.portfolio_vector[0] if features.portfolio_vector else 0.0, 0.0)
            + 0.15 * max(features.portfolio_vector[1] if features.portfolio_vector else 0.0, 0.0),
            0.0,
            1.0,
        )

    def _score_cross_market(self, features: FeatureEncoding) -> float:
        return _clip(
            0.70 * features.cross_market_pressure()
            + 0.15 * max(features.cross_market_vector[0] if features.cross_market_vector else 0.0, 0.0)
            + 0.15 * max(features.cross_market_vector[1] if features.cross_market_vector else 0.0, 0.0),
            0.0,
            1.0,
        )

    def _score_time(self, features: FeatureEncoding) -> float:
        return _clip(0.60 * features.time_sensitivity + 0.40 * (1.0 - features.uncertainty), 0.0, 1.0)

    def _uncertainty(self, features: FeatureEncoding, posterior_yes: float, posterior_no: float) -> float:
        posterior_gap = abs(posterior_yes - posterior_no)
        entropy_proxy = 1.0 - posterior_gap
        return _clip(0.55 * features.uncertainty + 0.45 * entropy_proxy, 0.0, 1.0)

    def _suggested_action(
        self,
        posterior_yes: float,
        posterior_no: float,
        context: BayesDecisionContext,
        uncertainty: float,
    ) -> str:
        if context.max_open_positions and context.open_positions >= context.max_open_positions:
            return "AVOID"
        # Only AVOID on very high uncertainty — 0.60 was too aggressive with no training data.
        # Once calibration data accumulates, this can tighten back to 0.65.
        if uncertainty >= 0.75:
            return "AVOID"

        yes_edge = None if context.yes_price is None else posterior_yes - context.yes_price
        no_edge = None if context.no_price is None else posterior_no - context.no_price

        # Prefer fewer, higher-conviction entries. The goal is to reduce the
        # number of marginal trades that are most likely to churn capital.
        if yes_edge is not None and yes_edge >= 0.12 and posterior_yes >= 0.68:
            return "BUY_YES"
        if no_edge is not None and no_edge >= 0.12 and posterior_no >= 0.68:
            return "BUY_NO"
        if posterior_yes >= 0.66 and (yes_edge is None or yes_edge >= 0.04):
            return "BUY_YES"
        if posterior_no >= 0.66 and (no_edge is None or no_edge >= 0.04):
            return "BUY_NO"
        return "HOLD"


_bayes_model: BayesModel | None = None


def get_bayes_model() -> BayesModel:
    global _bayes_model
    if _bayes_model is None:
        _bayes_model = BayesModel()
    return _bayes_model
