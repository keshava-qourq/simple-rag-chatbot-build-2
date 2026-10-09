"""Application configuration, loaded from the environment and `.env`.

The approved architecture names an OpenAI-compatible provider selected by
environment variables (provider, model, base URL, API key) and requires the
API to "read configuration and API key from .env and start cleanly with no
key set" (api_backend). `pydantic-settings` is the approved library for
exactly this. Nothing here calls out to the provider -- it only reads and
validates what `.env` sets, so `doc_processor`, `retrieval` and `answer_gen`
have one place to read it from instead of each reinventing `os.getenv`.

Validation here is deliberately split in two:

* Malformed values (wrong type, an overlap bigger than the chunk size, a base
  URL with no scheme, ...) are a configuration *bug* -- they fail fast with a
  `pydantic.ValidationError` naming the offending field, at process start
  (AC-063), never a bare stack trace from deep inside a request handler.
* A missing API key is not a bug, it is a valid "no provider configured yet"
  state the architecture explicitly allows. That shows up as an `issues`
  entry on `/config/status`, not a startup failure.
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Selects the OpenAI-compatible endpoint the `llm_provider` component calls.
    # Left unset, the API still starts; `answer_gen` is expected to emit the
    # plain no-model notice and `/config/status` reports `llm_configured:
    # false` instead of the app failing to boot. "openai" is the documented
    # default: any other value must still point at an OpenAI-compatible API.
    llm_provider: str = "openai"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_chat_model: str = "gpt-4o-mini"

    # Embeddings are configured independently of the chat model so a
    # deployment can point them at a different OpenAI-compatible endpoint.
    # When unset, `embedding_provider` falls back to `llm_provider` and, if
    # the two providers match, `effective_embedding_api_key` falls back to
    # `llm_api_key` -- the common case of one provider for both.
    embedding_provider: str = "openai"
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str = "text-embedding-3-small"

    # Retrieval and chunking knobs the approved `doc_processor` and `retrieval`
    # functions read. Documented defaults: chunk size ~800-1000 characters,
    # overlap ~15% of the chunk size, five chunks retrieved per question.
    # There is deliberately no endpoint to change these at runtime -- they
    # are read once, at process start, from the environment only.
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_top_k: int = 5

    # Matches the "<=~20MB" ceiling in the approved `POST /documents` endpoint.
    max_upload_mb: float = 20.0

    @field_validator("chunk_size")
    @classmethod
    def _validate_chunk_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("CHUNK_SIZE must be a positive integer")
        return value

    @field_validator("chunk_overlap")
    @classmethod
    def _validate_chunk_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("CHUNK_OVERLAP must be zero or a positive integer")
        return value

    @field_validator("retrieval_top_k")
    @classmethod
    def _validate_retrieval_top_k(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("RETRIEVAL_TOP_K must be a positive integer")
        return value

    @field_validator("max_upload_mb")
    @classmethod
    def _validate_max_upload_mb(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("MAX_UPLOAD_MB must be a positive number")
        return value

    @field_validator("llm_provider", "embedding_provider")
    @classmethod
    def _validate_provider(cls, value: str, info) -> str:
        if not value or not value.strip():
            raise ValueError(f"{info.field_name.upper()} must not be empty")
        return value

    @field_validator("llm_base_url", "embedding_base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None, info) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError(f"{info.field_name.upper()} must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _validate_overlap_within_chunk_size(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self

    @property
    def effective_embedding_api_key(self) -> str | None:
        """The key that actually authenticates the embedding call.

        Falls back to `llm_api_key` only when no embedding-specific key was
        given and the two providers are the same -- the common single-key
        setup -- so a deployment with two distinct providers is never
        silently credited with the wrong one's key.
        """
        if self.embedding_api_key:
            return self.embedding_api_key
        if self.embedding_provider == self.llm_provider:
            return self.llm_api_key
        return None

    @property
    def llm_configured(self) -> bool:
        """Whether an answer-generation model is usable right now."""
        return bool(self.llm_api_key)

    @property
    def embedding_configured(self) -> bool:
        """Whether an embedding model is usable right now."""
        return bool(self.effective_embedding_api_key)

    # Kept for any code that still asks the pre-US-022 question "is a model
    # configured at all" without distinguishing LLM from embeddings.
    @property
    def model_configured(self) -> bool:
        return self.llm_configured

    @property
    def issues(self) -> list[str]:
        """Readable, non-fatal configuration problems for `/config/status`.

        A missing key is a valid, expected state (the architecture requires
        the app to start cleanly with none set), so these are reported
        rather than raised.
        """
        problems: list[str] = []
        if not self.llm_api_key:
            problems.append("LLM_API_KEY is not set; the LLM provider is unconfigured.")
        if not self.effective_embedding_api_key:
            problems.append("EMBEDDING_API_KEY is not set; the embedding provider is unconfigured.")
        return problems


@lru_cache
def get_settings() -> Settings:
    """Cached: `.env` is read once per process, not once per request."""
    return Settings()


def redact_secret(message: str, settings: Settings | None = None) -> str:
    """Strip configured API key values out of a message before it is logged
    or returned to a caller (AC-067).

    Every call site that logs or surfaces an exception raised during a model
    or embedding call is expected to pass the exception text through this
    first -- a key appearing verbatim in a log line or an error response is
    exactly what this exists to prevent.
    """
    settings = settings or get_settings()
    redacted = message
    for secret in (settings.llm_api_key, settings.embedding_api_key):
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
