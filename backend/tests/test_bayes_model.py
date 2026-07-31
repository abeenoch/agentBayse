from types import SimpleNamespace

from app.services.bayes_model import BayesDecisionContext, BayesLinearPolicy, BayesPolicyInput, BayesModel
from app.services.feature_encoder import FeatureEncoding


def make_features() -> FeatureEncoding:
    return FeatureEncoding(
        market_id="m1",
        market_name="Test Market",
        event_type="crypto",
        topic_cluster="btc",
        resolution_clarity=0.8,
        sentiment_polarity=0.4,
        narrative_strength=0.9,
        uncertainty=0.2,
        time_sensitivity=0.7,
        news_relevance=0.8,
        contrarian_pressure=0.3,
        dependency_risk=0.2,
        market_regime="event_driven",
        key_facts=["fact"],
        risk_tags=[],
        semantic_vector=[0.8, 0.7, 0.9, 0.6, 0.7, 0.3, 0.8, 0.5],
        market_vector=[0.7, 0.6, 0.8, 0.7, 0.6, 0.5, 0.7, 0.4],
        portfolio_vector=[-0.2, -0.1, 0.1, 0.0],
        cross_market_vector=[0.1, 0.1, 0.2, 0.0],
        encoder_confidence=0.9,
    )


def test_bayes_model_generates_reasonable_posterior():
    model = BayesModel()
    output = model.infer(
        make_features(),
        BayesDecisionContext(yes_price=0.55, no_price=0.45, open_positions=1, max_open_positions=3),
    )

    assert 0.0 <= output.posterior_yes <= 1.0
    assert 0.0 <= output.posterior_no <= 1.0
    assert output.posterior_yes > 0.5
    assert output.suggested_action in {"BUY_YES", "BUY_NO", "HOLD", "AVOID"}
    assert output.breakdown["text_signal"] != 0


def test_bayes_model_updates_state():
    model = BayesModel()
    prior = model.state.prior_yes
    model.update_from_resolution(resolved_yes=True)
    assert model.state.prior_yes > prior
    model.update_from_resolution(resolved_yes=False)
    assert model.state.yes_updates == 1
    assert model.state.no_updates == 1


def test_bayes_model_is_conservative_on_marginal_edges():
    model = BayesModel()
    features = FeatureEncoding(
        market_id="m-conservative",
        market_name="Conservative Market",
        event_type="other",
        topic_cluster="other",
        resolution_clarity=0.5,
        sentiment_polarity=0.0,
        narrative_strength=0.0,
        uncertainty=0.5,
        time_sensitivity=0.5,
        news_relevance=0.5,
        contrarian_pressure=0.5,
        dependency_risk=0.5,
        market_regime="event_driven",
        key_facts=[],
        risk_tags=[],
        semantic_vector=[0.0] * 8,
        market_vector=[0.0] * 8,
        portfolio_vector=[0.0] * 4,
        cross_market_vector=[0.0] * 4,
        encoder_confidence=0.5,
    )

    output = model.infer(
        features,
        BayesDecisionContext(yes_price=0.52, no_price=0.48, open_positions=0, max_open_positions=3),
    )

    assert output.suggested_action in {"HOLD", "AVOID"}


def test_trained_policy_scores_candidates_from_artifact():
    run = SimpleNamespace(
        model_version="logreg_v1",
        feature_names=[
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
        ],
        coefficients={
            "bias": 0.8,
            "weights": [0.6, 1.2, 0.9, -0.5, 0.7, 0.4, 0.1, 0.1, 0.05, 0.05, -0.2, 0.1],
            "means": [0.0] * 13,
            "stds": [1.0] * 13,
        },
    )
    policy = BayesLinearPolicy.from_training_run(run)
    assert policy is not None

    candidate = BayesPolicyInput(
        signal_type="BUY_YES",
        confidence=88,
        estimated_probability=0.78,
        market_price=0.55,
        expected_value=9.0,
        rank_score=12.0,
        market_liquidity=2500.0,
        market_volume=12000.0,
        market_orders=45.0,
        market_spread=0.08,
        market_imbalance=0.04,
        snapshot_age_seconds=0.0,
    )
    posterior = policy.score_candidate(
        candidate,
        BayesDecisionContext(yes_price=0.55, no_price=0.45, open_positions=1, max_open_positions=3),
    )

    assert posterior.posterior_yes > 0.5
    assert posterior.suggested_action in {"BUY_YES", "BUY_NO", "HOLD", "AVOID"}
