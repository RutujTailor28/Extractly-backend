"""Tests for Gemini AI foundation (mocked — no real API calls by default)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ai.exceptions import (
    AIConfigurationError,
    AIConnectionError,
    AIModelError,
)
from app.services.ai.factory import clear_ai_provider_cache
from app.services.ai.foundation import AIHealthResult, AIModelInfo, AITextResult
from app.services.ai.gemini_provider import GeminiProvider, _safe_error_message


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_ai_provider_cache()
    yield
    clear_ai_provider_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_missing_gemini_key_raises() -> None:
    with pytest.raises(AIConfigurationError):
        GeminiProvider(api_key="", model="gemini-2.0-flash")


def test_safe_error_message_redacts_key(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "AQ.SuperSecretKeyValue123456")
    msg = _safe_error_message(
        Exception("auth failed for AQ.SuperSecretKeyValue123456"),
        "fallback",
    )
    assert "SuperSecret" not in msg
    assert "[REDACTED]" in msg


def test_health_unconfigured_when_not_gemini(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "mock")
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")
    response = client.get("/api/v1/ai/health")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["provider"] == "mock"
    # Must not leak secret material
    assert "AQ." not in str(body)
    assert config.settings.GEMINI_API_KEY not in str(body) if config.settings.GEMINI_API_KEY else True


def test_health_missing_key(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")
    response = client.get("/api/v1/ai/health")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert "GEMINI_API_KEY" in response.json()["message"]


def test_health_connected(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config.settings, "GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")

    mock_provider = MagicMock()
    mock_provider.health_check.return_value = AIHealthResult(
        provider="gemini",
        configured=True,
        connected=True,
        model="gemini-2.0-flash",
        message="AI provider is operational",
    )
    with patch("app.api.ai.get_ai_provider", return_value=mock_provider):
        response = client.get("/api/v1/ai/health")
    assert response.status_code == 200
    assert response.json()["connected"] is True


def test_list_models(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config.settings, "GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")

    mock_provider = MagicMock()
    mock_provider.name = "gemini"
    mock_provider.list_models.return_value = [
        AIModelInfo(
            name="gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            supported_generation_methods=["generateContent"],
        )
    ]
    with patch("app.api.ai.get_ai_provider", return_value=mock_provider):
        response = client.get("/api/v1/ai/models")
    assert response.status_code == 200
    body = response.json()
    assert body["models"][0]["name"] == "gemini-2.0-flash"
    assert "test-key" not in str(body)


def test_generate_text_endpoint(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")

    mock_provider = MagicMock()
    mock_provider.generate_text.return_value = AITextResult(
        provider="gemini",
        model="gemini-2.0-flash",
        prompt="Reply with exactly: Gemini connection successful",
        text="Gemini connection successful",
        latency_ms=12,
    )
    with patch("app.api.ai.get_ai_provider", return_value=mock_provider):
        response = client.post(
            "/api/v1/ai/test",
            json={"prompt": "Reply with exactly: Gemini connection successful"},
        )
    assert response.status_code == 200
    assert response.json()["response"] == "Gemini connection successful"
    assert response.json()["note"] == "development-only endpoint"


def test_invalid_model_mapped() -> None:
    client = MagicMock()
    client.models.generate_content.side_effect = Exception(
        "404 models/not-a-real-model is not found"
    )
    provider = GeminiProvider(
        api_key="test-key",
        model="not-a-real-model",
        client=client,
    )
    with pytest.raises(AIModelError):
        provider.generate_text("hi")


def test_network_failure_mapped() -> None:
    client = MagicMock()
    client.models.list.side_effect = Exception("connection timeout to generativelanguage")
    provider = GeminiProvider(
        api_key="test-key",
        model="gemini-2.0-flash",
        client=client,
    )
    with pytest.raises(AIConnectionError):
        provider.list_models()


def test_list_models_parses_sdk_objects() -> None:
    client = MagicMock()
    client.models.list.return_value = [
        SimpleNamespace(
            name="models/gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            supported_actions=["generateContent"],
            input_token_limit=1048576,
            output_token_limit=8192,
            description="Fast",
            version="2.0",
        )
    ]
    provider = GeminiProvider(
        api_key="test-key",
        model="gemini-2.0-flash",
        client=client,
    )
    models = provider.list_models()
    assert models[0].name == "gemini-2.0-flash"
    assert "generateContent" in models[0].supported_generation_methods


def test_ai_routes_hidden_outside_development(client: TestClient, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "ENVIRONMENT", "production")
    assert client.get("/api/v1/ai/health").status_code == 404
    assert client.get("/api/v1/ai/models").status_code == 404


def test_responses_never_include_api_key(client: TestClient, monkeypatch) -> None:
    from app.core import config

    secret = "AQ.ThisMustNeverAppearInResponses12345"
    monkeypatch.setattr(config.settings, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", secret)
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")
    mock_provider = MagicMock()
    mock_provider.health_check.return_value = AIHealthResult(
        provider="gemini",
        configured=True,
        connected=False,
        model="x",
        message="Gemini authentication failed. Check GEMINI_API_KEY.",
    )
    with patch("app.api.ai.get_ai_provider", return_value=mock_provider):
        body = client.get("/api/v1/ai/health").text
    assert secret not in body
