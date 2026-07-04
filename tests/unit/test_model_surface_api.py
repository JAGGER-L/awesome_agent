from __future__ import annotations

from fastapi.testclient import TestClient
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app


def test_models_endpoint_returns_safe_routing_facts() -> None:
    client = TestClient(
        create_app(
            settings=test_settings(
                deepseek_api_key="secret-value",
                leader_model="deepseek-v4-pro",
            )
        )
    )

    response = client.get("/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current"] == {
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-pro",
    }
    [provider] = payload["providers"]
    assert provider["id"] == "deepseek"
    assert provider["display_name"] == "DeepSeek"
    assert provider["configured"] is True
    assert provider["credential_env"] == "AWESOME_AGENT_DEEPSEEK_API_KEY"
    assert provider["api_key_present"] is True
    assert [model["id"] for model in provider["models"]] == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    assert "secret-value" not in response.text
    assert "base_url" not in provider
    forbidden = {"price", "cost", "amount", "billing", "currency", "usd"}
    assert forbidden.isdisjoint(set(provider))
