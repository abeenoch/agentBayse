from app.services.scheduler import _matches_watchlist


def test_watchlist_matches_btc_15m():
    event = {"title": "BTC 15m price direction"}
    market = {"title": "BTC 15-minute outlook"}
    assert _matches_watchlist(event, market)


def test_watchlist_matches_fx():
    event = {"title": "Dollar to Naira tomorrow"}
    market = {"title": "USD/NGN rate"}
    assert _matches_watchlist(event, market)


def test_watchlist_rejects_irrelevant():
    event = {"title": "Elections in Europe"}
    market = {"title": "Who wins?"}
    assert not _matches_watchlist(event, market)


def test_watchlist_matches_temperature_nigeria():
    event = {"title": "Temperature in Lagos next week", "description": "Nigeria weather"}
    market = {"title": "Lagos temp"}
    assert _matches_watchlist(event, market)


def test_watchlist_matches_usd_gbp_cross():
    event = {"title": "GBP/USD rate tomorrow"}
    market = {"title": "USD to GBP close"}
    assert _matches_watchlist(event, market)
