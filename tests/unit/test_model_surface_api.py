from __future__ import annotations

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.settings import Settings


def test_models_endpoint_returns_safe_routing_facts() -> None:
    client = TestClient(
        create_app(
            settings=Settings(
                _env_file=None,
                deepseek_api_key="secret-value",
                deepseek_base_url="https://gateway.example/v1",
                leader_model="deepseek-v4-pro",
            )
        )
    )

    response = client.get("/models")

    assert response.status_code == 200
    payload = response.json()
    leader = payload[0]
    assert leader["name"] == "deepseek-v4-pro"
    assert leader["provider"] == "deepseek"
    assert leader["configured"] is True
    assert leader["api_key_env"] == "AWESOME_AGENT_DEEPSEEK_API_KEY"
    assert leader["api_key_present"] is True
    assert leader["base_url"] == "https://gateway.example/v1"
    assert "secret-value" not in response.text
    forbidden = {"price", "cost", "amount", "billing", "currency", "usd"}
    assert forbidden.isdisjoint(set(leader))
