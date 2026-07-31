from __future__ import annotations

import json
import re
from datetime import datetime
from math import tanh
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.services.llm_client import call_llm
from app.utils.logger import logger

SCHEMA_VERSION = "v1"
SEMANTIC_VECTOR_SIZE = 8
MARKET_VECTOR_SIZE = 8
PORTFOLIO_VECTOR_SIZE = 4
CROSS_MARKET_VECTOR_SIZE = 4


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _vector_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _vector_l2(values: list[float]) -> float:
    return sum(v * v for v in values) ** 0.5


def _normalize_vector(values: Any, size: int, default: float = 0.0) -> list[float]:
    if not isinstance(values, list):
        values = []
    out: list[float] = []
    for item in values[:size]:
        try:
            out.append(_clip(float(item), -1.0, 1.0))
        except Exception:
            out.append(default)
    while len(out) < size:
        out.append(default)
    return out


def _extract_json(text: str) -> dict[str, Any]:
    clean = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in encoder response")
    return json.loads(match.group())


class FeatureEncoding(BaseModel):
    schema_version: str = Field(default=SCHEMA_VERSION)
    market_id: str
    market_name: str
    event_type: str = "other"
    topic_cluster: str = "other"
    resolution_clarity: float = Field(default=0.5, ge=0.0, le=1.0)
    sentiment_polarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    narrative_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    time_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    news_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    contrarian_pressure: float = Field(default=0.5, ge=0.0, le=1.0)
    dependency_risk: float = Field(default=0.5, ge=0.0, le=1.0)
    market_regime: str = "event_driven"
    key_facts: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    semantic_vector: list[float] = Field(default_factory=list)
    market_vector: list[float] = Field(default_factory=list)
    portfolio_vector: list[float] = Field(default_factory=list)
    cross_market_vector: list[float] = Field(default_factory=list)
    encoder_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("semantic_vector")
    @classmethod
    def _validate_semantic_vector(cls, value: list[float]) -> list[float]:
        return _normalize_vector(value, SEMANTIC_VECTOR_SIZE)

    @field_validator("market_vector")
    @classmethod
    def _validate_market_vector(cls, value: list[float]) -> list[float]:
        return _normalize_vector(value, MARKET_VECTOR_SIZE)

    @field_validator("portfolio_vector")
    @classmethod
    def _validate_portfolio_vector(cls, value: list[float]) -> list[float]:
        return _normalize_vector(value, PORTFOLIO_VECTOR_SIZE)

    @field_validator("cross_market_vector")
    @classmethod
    def _validate_cross_market_vector(cls, value: list[float]) -> list[float]:
        return _normalize_vector(value, CROSS_MARKET_VECTOR_SIZE)

    def _vector_strength(self, values: list[float]) -> float:
        """
        Interpret the vector as either:
        - signed evidence in [-1, 1], or
        - unsigned strength in [0, 1] when the model emits only non-negative values.

        This keeps the encoder stable across LLM and fallback paths.
        """
        if not values:
            return 0.0

        clipped = [_clip(float(v), -1.0, 1.0) for v in values]
        if min(clipped) >= 0.0:
            return _clip(_vector_mean(clipped), 0.0, 1.0)
        return _clip((_vector_mean(clipped) + 1.0) / 2.0, 0.0, 1.0)

    def semantic_strength(self) -> float:
        return self._vector_strength(self.semantic_vector)

    def market_strength(self) -> float:
        return self._vector_strength(self.market_vector)

    def portfolio_pressure(self) -> float:
        return self._vector_strength(self.portfolio_vector)

    def cross_market_pressure(self) -> float:
        return self._vector_strength(self.cross_market_vector)


class FeatureEncoder:
    def __init__(self):
        self.model_name = getattr(settings, "ai_provider", "unknown")

    def build_prompt(self, context: Mapping[str, Any]) -> tuple[str, str]:
        system = (
            "You are a Bayse feature encoder. "
            "Return a single JSON object that matches the agreed schema exactly. "
            "Do not output a trade decision. "
            f"Schema version: {SCHEMA_VERSION}."
        )
        user_lines = [
            "Encode the market context into the fixed four-vector feature schema.",
            "Use signed vectors in the range [-1, 1] where -1 is strong evidence against, 0 is neutral, and 1 is strong evidence for.",
            f"market_id: {context.get('market_id', '')}",
            f"market_name: {context.get('market_name', '')}",
            f"event_type: {context.get('event_type', 'other')}",
            f"topic_cluster: {context.get('topic_cluster', 'other')}",
            f"yes_price: {context.get('yes_price', 'n/a')}",
            f"no_price: {context.get('no_price', 'n/a')}",
            f"time_remaining: {context.get('time_remaining', 'unknown')}",
            f"timeframe: {context.get('timeframe', 'unknown')}",
            f"portfolio: {json.dumps(context.get('portfolio', {}), ensure_ascii=False)}",
            f"history: {json.dumps(context.get('history', []), ensure_ascii=False)}",
            f"news_snippets: {json.dumps(context.get('snippets', []), ensure_ascii=False)}",
            f"rag_chunks: {json.dumps(context.get('rag_chunks', []), ensure_ascii=False)}",
            "Return keys: market_id, market_name, event_type, topic_cluster, resolution_clarity, sentiment_polarity, narrative_strength, uncertainty, time_sensitivity, news_relevance, contrarian_pressure, dependency_risk, market_regime, key_facts, risk_tags, semantic_vector, market_vector, portfolio_vector, cross_market_vector, encoder_confidence.",
        ]
        return system, "\n".join(user_lines)

    async def encode(self, context: Mapping[str, Any]) -> FeatureEncoding:
        system, prompt = self.build_prompt(context)
        raw_text = ""
        if not settings.mock_mode:
            try:
                raw_text = await call_llm(prompt, system=system)
                payload = _extract_json(raw_text)
                encoded = self._normalize_payload(payload, context)
                return encoded
            except Exception as exc:
                logger.warning("Feature encoder LLM path failed; using fallback. err=%s", exc)

        return self._fallback_encoding(context)

    def _normalize_payload(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> FeatureEncoding:
        data = {
            "market_id": str(payload.get("market_id") or context.get("market_id") or ""),
            "market_name": str(payload.get("market_name") or context.get("market_name") or ""),
            "event_type": str(payload.get("event_type") or context.get("event_type") or "other"),
            "topic_cluster": str(payload.get("topic_cluster") or context.get("topic_cluster") or "other"),
            "resolution_clarity": _clip(float(payload.get("resolution_clarity", 0.5)), 0.0, 1.0),
            "sentiment_polarity": _clip(float(payload.get("sentiment_polarity", 0.0)), -1.0, 1.0),
            "narrative_strength": _clip(float(payload.get("narrative_strength", 0.5)), 0.0, 1.0),
            "uncertainty": _clip(float(payload.get("uncertainty", 0.5)), 0.0, 1.0),
            "time_sensitivity": _clip(float(payload.get("time_sensitivity", 0.5)), 0.0, 1.0),
            "news_relevance": _clip(float(payload.get("news_relevance", 0.5)), 0.0, 1.0),
            "contrarian_pressure": _clip(float(payload.get("contrarian_pressure", 0.5)), 0.0, 1.0),
            "dependency_risk": _clip(float(payload.get("dependency_risk", 0.5)), 0.0, 1.0),
            "market_regime": str(payload.get("market_regime") or "event_driven"),
            "key_facts": [str(x) for x in (payload.get("key_facts") or []) if str(x).strip()],
            "risk_tags": [str(x) for x in (payload.get("risk_tags") or []) if str(x).strip()],
            "semantic_vector": _normalize_vector(payload.get("semantic_vector"), SEMANTIC_VECTOR_SIZE),
            "market_vector": _normalize_vector(payload.get("market_vector"), MARKET_VECTOR_SIZE),
            "portfolio_vector": _normalize_vector(payload.get("portfolio_vector"), PORTFOLIO_VECTOR_SIZE),
            "cross_market_vector": _normalize_vector(payload.get("cross_market_vector"), CROSS_MARKET_VECTOR_SIZE),
            "encoder_confidence": _clip(float(payload.get("encoder_confidence", 0.5)), 0.0, 1.0),
        }
        if not data["market_id"] or not data["market_name"]:
            raise ValueError("Encoder payload missing market_id or market_name")
        return FeatureEncoding(**data)

    def _fallback_encoding(self, context: Mapping[str, Any]) -> FeatureEncoding:
        text_parts = [
            str(context.get("market_name", "")),
            str(context.get("description", "")),
            " ".join(str(x) for x in context.get("snippets", []) or []),
            " ".join(str(x) for x in context.get("rag_chunks", []) or []),
        ]
        text = " ".join(text_parts).lower()
        yes_price = self._as_float(context.get("yes_price"), 0.5)
        no_price = self._as_float(context.get("no_price"), 1.0 - yes_price)
        time_remaining = self._as_float(context.get("time_remaining_seconds"), 0.0)
        portfolio = context.get("portfolio", {}) if isinstance(context.get("portfolio"), dict) else {}
        available = self._as_float(portfolio.get("available_to_deploy"), 0.0)
        deployed = self._as_float(portfolio.get("deployed"), 0.0)
        open_positions = self._as_float(portfolio.get("open_positions"), 0.0)
        source_count = len(context.get("snippets", []) or []) + len(context.get("rag_chunks", []) or [])

        sentiment = self._sentiment_score(text)
        uncertainty = _clip(0.65 + text.count("?") * 0.04 + text.count("maybe") * 0.05 - abs(sentiment) * 0.1, 0.0, 1.0)
        narrative = _clip(0.35 + min(len(text) / 1400.0, 0.45) + abs(sentiment) * 0.15, 0.0, 1.0)
        resolution = _clip(0.5 + (0.15 if "rules" in text else 0.0) - uncertainty * 0.2, 0.0, 1.0)
        time_sensitivity = _clip(0.25 + (1.0 if time_remaining and time_remaining < 7200 else 0.0) * 0.45, 0.0, 1.0)
        news_relevance = _clip(0.4 + min(source_count / 8.0, 0.35), 0.0, 1.0)
        contrarian = _clip(0.5 + max(0.0, abs(0.5 - yes_price) - 0.1) * 0.8, 0.0, 1.0)
        dependency = _clip(float(context.get("dependency_risk", 0.5)), 0.0, 1.0)
        market_regime = "event_driven" if time_remaining and time_remaining < 86400 else "trend"
        encoder_confidence = _clip(0.45 + narrative * 0.25 + news_relevance * 0.15 - uncertainty * 0.1, 0.0, 1.0)

        semantic_vector = [
            sentiment,
            narrative,
            1.0 - uncertainty,
            resolution,
            news_relevance,
            contrarian,
            encoder_confidence,
            self._fact_density(context),
        ]
        market_vector = [
            _clip((0.5 - yes_price) * 2.0, -1.0, 1.0),
            _clip((0.5 - no_price) * 2.0, -1.0, 1.0),
            _clip(1.0 - uncertainty, 0.0, 1.0),
            _clip(news_relevance * 2.0 - 1.0, -1.0, 1.0),
            _clip(time_sensitivity * 2.0 - 1.0, -1.0, 1.0),
            _clip(self._scale_volume(context.get("volume", 0.0)), -1.0, 1.0),
            _clip(self._scale_liquidity(context.get("liquidity", 0.0)), -1.0, 1.0),
            _clip((0.5 - uncertainty) * 2.0, -1.0, 1.0),
        ]
        portfolio_vector = [
            _clip(self._scale_amount(available), -1.0, 1.0),
            _clip(self._scale_amount(deployed), -1.0, 1.0),
            _clip(1.0 - min(open_positions / 5.0, 1.0), -1.0, 1.0),
            _clip(1.0 - context.get("risk_buffer", 0.5), -1.0, 1.0),
        ]
        cross_market_vector = [
            _clip(1.0 - dependency * 2.0, -1.0, 1.0),
            _clip(1.0 - contrarian * 2.0, -1.0, 1.0),
            _clip(1.0 - uncertainty * 2.0, -1.0, 1.0),
            _clip(self._duplication_risk(context), -1.0, 1.0),
        ]

        return FeatureEncoding(
            market_id=str(context.get("market_id") or ""),
            market_name=str(context.get("market_name") or ""),
            event_type=str(context.get("event_type") or "other"),
            topic_cluster=str(context.get("topic_cluster") or "other"),
            resolution_clarity=resolution,
            sentiment_polarity=sentiment,
            narrative_strength=narrative,
            uncertainty=uncertainty,
            time_sensitivity=time_sensitivity,
            news_relevance=news_relevance,
            contrarian_pressure=contrarian,
            dependency_risk=dependency,
            market_regime=market_regime,
            key_facts=[x for x in self._extract_facts(context)],
            risk_tags=self._risk_tags(context, uncertainty, time_sensitivity, dependency),
            semantic_vector=semantic_vector,
            market_vector=market_vector,
            portfolio_vector=portfolio_vector,
            cross_market_vector=cross_market_vector,
            encoder_confidence=encoder_confidence,
        )

    def _sentiment_score(self, text: str) -> float:
        positive = ("surge", "gain", "win", "support", "bull", "strong", "qualify", "approve", "rise")
        negative = ("drop", "risk", "delay", "loss", "bear", "weak", "deny", "reject", "problem")
        pos = sum(text.count(word) for word in positive)
        neg = sum(text.count(word) for word in negative)
        raw = (pos - neg) / max(pos + neg, 1)
        return _clip(raw, -1.0, 1.0)

    def _fact_density(self, context: Mapping[str, Any]) -> float:
        facts = context.get("key_facts") or []
        if not isinstance(facts, list):
            facts = []
        return _clip(len(facts) / 6.0, 0.0, 1.0)

    def _extract_facts(self, context: Mapping[str, Any]) -> list[str]:
        facts = context.get("key_facts") or []
        if isinstance(facts, list) and facts:
            return [str(x) for x in facts if str(x).strip()]
        snippets = context.get("snippets") or []
        out = []
        for item in snippets[:3]:
            text = str(item).strip()
            if text:
                out.append(text[:180])
        return out

    def _risk_tags(self, context: Mapping[str, Any], uncertainty: float, time_sensitivity: float, dependency: float) -> list[str]:
        tags = []
        if uncertainty > 0.7:
            tags.append("high_uncertainty")
        if time_sensitivity > 0.7:
            tags.append("time_sensitive")
        if dependency > 0.6:
            tags.append("cross_market_dependency")
        if self._as_float(context.get("liquidity"), 0.0) < 1000:
            tags.append("low_liquidity")
        if not context.get("snippets") and not context.get("rag_chunks"):
            tags.append("thin_evidence")
        return tags

    def _scale_volume(self, value: Any) -> float:
        amount = self._as_float(value, 0.0)
        return tanh(amount / 10000.0)

    def _scale_liquidity(self, value: Any) -> float:
        amount = self._as_float(value, 0.0)
        return tanh(amount / 10000.0)

    def _scale_amount(self, value: Any) -> float:
        amount = self._as_float(value, 0.0)
        return tanh(amount / 100000.0)

    def _duplication_risk(self, context: Mapping[str, Any]) -> float:
        history = context.get("history") or []
        if not isinstance(history, list):
            return 0.0
        repeats = sum(1 for item in history if isinstance(item, dict) and item.get("signal") in {"BUY_YES", "BUY_NO"})
        return _clip(repeats / 5.0, 0.0, 1.0)

    def _as_float(self, value: Any, default: float) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, str) and not value.strip():
                return default
            return float(value)
        except Exception:
            return default


_feature_encoder: FeatureEncoder | None = None


def get_feature_encoder() -> FeatureEncoder:
    global _feature_encoder
    if _feature_encoder is None:
        _feature_encoder = FeatureEncoder()
    return _feature_encoder
