import pytest

from app.services.feature_encoder import FeatureEncoder, FeatureEncoding


@pytest.mark.asyncio
async def test_feature_encoder_normalizes_llm_payload(monkeypatch):
    async def fake_llm(prompt, system=""):
        return """
        {
          "market_id": "m1",
          "market_name": "Test Market",
          "event_type": "crypto",
          "topic_cluster": "btc",
          "resolution_clarity": 0.9,
          "sentiment_polarity": 0.25,
          "narrative_strength": 0.8,
          "uncertainty": 0.1,
          "time_sensitivity": 0.7,
          "news_relevance": 0.85,
          "contrarian_pressure": 0.6,
          "dependency_risk": 0.2,
          "market_regime": "event_driven",
          "key_facts": ["fact 1", "fact 2"],
          "risk_tags": ["high_volatility"],
          "semantic_vector": [1, 0, 0, 0, 0, 0, 0, 0],
          "market_vector": [0, 1, 0, 0, 0, 0, 0, 0],
          "portfolio_vector": [0, 0, 1, 0],
          "cross_market_vector": [0, 0, 0, 1],
          "encoder_confidence": 0.88
        }
        """

    monkeypatch.setattr("app.services.feature_encoder.settings.mock_mode", False)
    monkeypatch.setattr("app.services.feature_encoder.call_llm", fake_llm)

    encoder = FeatureEncoder()
    output = await encoder.encode(
        {
            "market_id": "m1",
            "market_name": "Test Market",
            "description": "crypto market",
            "snippets": ["some news"],
            "yes_price": 0.55,
            "no_price": 0.45,
            "portfolio": {"open_positions": 1},
        }
    )

    assert isinstance(output, FeatureEncoding)
    assert output.market_id == "m1"
    assert len(output.semantic_vector) == 8
    assert len(output.market_vector) == 8
    assert len(output.portfolio_vector) == 4
    assert len(output.cross_market_vector) == 4


@pytest.mark.asyncio
async def test_feature_encoder_falls_back_on_bad_payload(monkeypatch):
    async def fake_llm(prompt, system=""):
        return "not json"

    monkeypatch.setattr("app.services.feature_encoder.settings.mock_mode", False)
    monkeypatch.setattr("app.services.feature_encoder.call_llm", fake_llm)

    encoder = FeatureEncoder()
    output = await encoder.encode(
        {
            "market_id": "m2",
            "market_name": "Fallback Market",
            "description": "something happened",
            "snippets": [],
            "yes_price": 0.5,
            "no_price": 0.5,
        }
    )

    assert output.market_id == "m2"
    assert output.market_name == "Fallback Market"
    assert len(output.semantic_vector) == 8


def test_feature_encoder_vector_strength_is_adaptive():
    unsigned = FeatureEncoding(
        market_id="m1",
        market_name="Unsigned",
        semantic_vector=[0, 0, 1, 1, 0, 0, 1, 0],
        market_vector=[0, 1, 0, 1, 0, 0, 0, 0],
        portfolio_vector=[0, 0, 1, 0],
        cross_market_vector=[0, 0, 0, 1],
    )
    signed = FeatureEncoding(
        market_id="m2",
        market_name="Signed",
        semantic_vector=[-1, -1, 1, 1, -1, -1, 1, -1],
        market_vector=[-1, 1, -1, 1, -1, -1, -1, -1],
        portfolio_vector=[-1, -1, 1, -1],
        cross_market_vector=[-1, -1, -1, 1],
    )

    assert unsigned.semantic_strength() == pytest.approx(0.375)
    assert unsigned.market_strength() == pytest.approx(0.25)
    assert signed.semantic_strength() == pytest.approx(0.375)
    assert signed.market_strength() == pytest.approx(0.25)
