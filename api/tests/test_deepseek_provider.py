"""DeepSeek runs through the OpenAI-compatible provider with its own base URL."""

import json
from types import SimpleNamespace

import pytest

from app.services import ai_providers
from app.services.ai_deployment import AIDeploymentService
from app.services.ai_providers import JSON_OBJECT_WRAPPER_INSTRUCTION, OpenAIProvider, create_provider


def _settings(**overrides):
    base = dict(ai_provider="deepseek", deepseek_api_key="sk-test", deepseek_model="deepseek-flash",
                deepseek_base_url="https://api.deepseek.com")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_factory_builds_a_deepseek_provider():
    provider = create_provider(_settings())
    assert isinstance(provider, OpenAIProvider)
    assert provider.provider_name == "deepseek"
    assert provider.model == "deepseek-flash"
    assert str(provider.client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_model_is_configurable():
    assert create_provider(_settings(deepseek_model="deepseek-v4-pro")).model == "deepseek-v4-pro"


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key_disables_ai(key):
    assert create_provider(_settings(deepseek_api_key=key)) is None


def test_generate_asks_for_the_manifests_wrapper_and_reports_usage():
    provider = create_provider(_settings())
    sent = {}
    manifests = [{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web"}}]

    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"manifests": manifests})))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300),
        )

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    response = provider.generate("Return ONLY a JSON array.", "compose here")

    assert sent["model"] == "deepseek-flash"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["content"].endswith(JSON_OBJECT_WRAPPER_INSTRUCTION)
    assert response.provider == "deepseek" and response.input_tokens == 1200

    parsed = AIDeploymentService(None, None, None, provider=None, enabled=False)._parse_ai_response(response.text)
    assert parsed == manifests


def test_errors_name_the_provider():
    provider = create_provider(_settings())

    def boom(**kwargs):
        raise RuntimeError("401 invalid key")

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(ai_providers.LLMProviderError, match="deepseek call failed: 401 invalid key"):
        provider.generate("s", "u")


def test_openai_provider_is_unchanged_by_default():
    provider = create_provider(SimpleNamespace(ai_provider="openai", openai_api_key="sk", openai_model="gpt-4o"))
    assert provider.provider_name == "openai"
    assert "api.openai.com" in str(provider.client.base_url)
