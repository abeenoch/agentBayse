import pytest

from app.services.bayse_client import BayseClient


@pytest.mark.asyncio
async def test_client_instantiates():
    client = BayseClient()
    assert client.base_url.endswith("/v1")
