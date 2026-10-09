"""Tests for the configuration layer and `GET /config/status`.

Covers AC-062 through AC-067: env-driven provider/model/base-URL selection,
readable errors on malformed values, documented chunking/retrieval defaults
with no runtime mutation surface, and that no key ever appears in the
`/config/status` response or in a redacted message.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings, redact_secret
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_start_cleanly_with_no_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-062/AC-063: no env set at all still produces a valid, usable Settings."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openai"
    assert settings.llm_chat_model == "gpt-4o-mini"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.llm_configured is False
    assert settings.embedding_configured is False


def test_documented_chunking_and_retrieval_defaults() -> None:
    """AC-064: chunk size ~800-1000, overlap ~15%, a retrieved-chunk count."""
    settings = Settings(_env_file=None)

    assert 800 <= settings.chunk_size <= 1000
    assert settings.chunk_overlap == pytest.approx(settings.chunk_size * 0.15, rel=0.05)
    assert settings.retrieval_top_k > 0


def test_malformed_chunk_size_names_the_offending_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-063: a malformed value raises a readable error naming the variable."""
    monkeypatch.setenv("CHUNK_SIZE", "not-a-number")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "chunk_size" in str(exc_info.value)


def test_overlap_not_smaller_than_chunk_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHUNK_SIZE", "500")
    monkeypatch.setenv("CHUNK_OVERLAP", "500")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "CHUNK_OVERLAP" in str(exc_info.value)


def test_malformed_base_url_names_the_offending_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "not-a-url")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "LLM_BASE_URL" in str(exc_info.value)


def test_config_status_reports_unconfigured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    response = client.get("/config/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "llm_configured": False,
        "embedding_configured": False,
        "llm_model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "issues": [
            "LLM_API_KEY is not set; the LLM provider is unconfigured.",
            "EMBEDDING_API_KEY is not set; the embedding provider is unconfigured.",
        ],
    }


def test_config_status_reports_configured_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-value")

    response = client.get("/config/status")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_configured"] is True
    # Same provider, no embedding-specific key given -> falls back to the LLM key.
    assert body["embedding_configured"] is True
    assert body["issues"] == []


def test_config_status_never_echoes_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-super-secret-value"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("EMBEDDING_API_KEY", secret)

    response = client.get("/config/status")

    assert secret not in response.text


def test_redact_secret_strips_the_key_from_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-super-secret-value"
    monkeypatch.setenv("LLM_API_KEY", secret)
    settings = Settings(_env_file=None)

    message = f"Request to provider failed, Authorization: Bearer {secret}"
    redacted = redact_secret(message, settings=settings)

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_no_runtime_endpoint_mutates_chunking_or_retrieval_config() -> None:
    """AC-064: no API surface exists to change these at runtime."""
    response = client.get("/openapi.json")
    paths = response.json()["paths"]

    assert "/config" not in paths
    for path, methods in paths.items():
        assert "put" not in methods or "config" not in path
        assert "patch" not in methods or "config" not in path
