import pytest

import app.main as main_module


class _FakeSession:
    pass


class _FakeSessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_startup_reconciliation_runs_before_scheduler(monkeypatch):
    calls: list[str] = []
    session = _FakeSession()

    async def fake_init_db():
        calls.append("init_db")

    async def fake_normalize_terminal_trades(received_session):
        calls.append("normalize")
        assert received_session is session
        return 2

    async def fake_reconcile_open_trades(received_session, client):
        calls.append("reconcile")
        assert received_session is session
        assert client == "fake-client"
        return 3, 1

    def fake_get_bayse_client():
        calls.append("client")
        return "fake-client"

    def fake_start_scheduler():
        calls.append("scheduler")

    monkeypatch.setattr(main_module, "init_db", fake_init_db)
    monkeypatch.setattr(main_module, "normalize_terminal_trades", fake_normalize_terminal_trades)
    monkeypatch.setattr(main_module, "reconcile_open_trades", fake_reconcile_open_trades)
    monkeypatch.setattr(main_module, "get_bayse_client", fake_get_bayse_client)
    monkeypatch.setattr(main_module, "start_scheduler", fake_start_scheduler)
    monkeypatch.setattr(main_module, "AsyncSessionLocal", _FakeSessionFactory(session))

    startup_handler = main_module.app.router.on_startup[0]
    await startup_handler()

    assert calls == ["init_db", "normalize", "client", "reconcile", "scheduler"]
