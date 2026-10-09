"""Embedding client for document chunks (US-003-2).

Reads only `app.config.Settings` -- `embedding_provider`, `embedding_base_url`,
`embedding_model` and `effective_embedding_api_key` -- and calls an
OpenAI-compatible `/embeddings` endpoint with the official `openai` SDK,
which is also how `embedding_base_url` lets a deployment point at any
OpenAI-compatible provider, not only OpenAI itself.

Every failure path here raises `EmbeddingError` with a message that is
already human-readable and already passed through `redact_secret` -- never
the provider's raw exception text, which can (and for some providers does)
echo the API key back in its message. `doc_processor` is the only caller,
and only needs to catch `EmbeddingError`.
"""

import logging

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
)

from app.config import Settings, redact_secret

logger = logging.getLogger(__name__)

NOT_CONFIGURED_MESSAGE = (
    "The embedding model is not configured. Set EMBEDDING_API_KEY (or LLM_API_KEY, "
    "if the embedding provider matches the LLM provider) and try again."
)
UNREACHABLE_MESSAGE = (
    "The embedding model could not be reached. Check EMBEDDING_BASE_URL and network "
    "connectivity, then try again."
)
AUTH_MESSAGE = (
    "The embedding provider rejected the configured API key. Check EMBEDDING_API_KEY "
    "and try again."
)
GENERIC_FAILURE_MESSAGE = (
    "The embedding model could not process this document. Try again later."
)


class EmbeddingError(Exception):
    """Carries only the human-readable message a caller is allowed to see."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    """Embed each string in `texts`, preserving order.

    Raises `EmbeddingError` -- never a bare provider exception -- when the
    embedding model is not configured, unreachable, rejects the key, or
    otherwise fails. Returns an empty list for an empty `texts` without
    making a call.
    """
    if not settings.embedding_configured:
        raise EmbeddingError(NOT_CONFIGURED_MESSAGE)
    if not texts:
        return []

    client = OpenAI(
        api_key=settings.effective_embedding_api_key,
        base_url=settings.embedding_base_url or None,
    )

    try:
        response = client.embeddings.create(model=settings.embedding_model, input=texts)
    except AuthenticationError as exc:
        logger.warning("Embedding auth failure: %s", redact_secret(str(exc), settings))
        raise EmbeddingError(AUTH_MESSAGE) from exc
    except APIConnectionError as exc:
        logger.warning("Embedding connection failure: %s", redact_secret(str(exc), settings))
        raise EmbeddingError(UNREACHABLE_MESSAGE) from exc
    except (APIStatusError, APIError) as exc:
        logger.warning("Embedding API failure: %s", redact_secret(str(exc), settings))
        raise EmbeddingError(GENERIC_FAILURE_MESSAGE) from exc
    except Exception as exc:  # pragma: no cover - unexpected transport/SDK failure
        logger.warning("Embedding unexpected failure: %s", redact_secret(str(exc), settings))
        raise EmbeddingError(GENERIC_FAILURE_MESSAGE) from exc

    by_index = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in by_index]
