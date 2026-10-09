"""Application configuration, loaded from the environment and `.env`.

The approved architecture names an OpenAI-compatible provider selected by
environment variables (provider, model, base URL, API key) and requires the
API to "read configuration and API key from .env and start cleanly with no
key set" (api_backend). `pydantic-settings` is the approved library for
exactly this. Nothing here calls out to the provider -- it only reads and
validates what `.env` sets, so `doc_processor`, `retrieval` and `answer_gen`
have one place to read it from instead of each reinventing `os.getenv`.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Selects the OpenAI-compatible endpoint the `llm_provider` component calls.
    # Left unset, the API still starts; `answer_gen` is expected to emit the
    # plain no-model notice and `/config/status` reports `model_configured:
    # false` instead of the app failing to boot.
    llm_provider: str = "openai"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_chat_model: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    # Retrieval and chunking knobs the approved `doc_processor` and `retrieval`
    # functions read; reasonable starting points, not tuned values.
    chunk_size: int = 1000
    chunk_overlap: int = 200
    retrieval_top_k: int = 5

    # Matches the "<=~20MB" ceiling in the approved `POST /documents` endpoint.
    max_upload_mb: float = 20.0

    @property
    def model_configured(self) -> bool:
        """Whether an answer-generation model is usable right now.

        A plain presence check on the API key: the architecture ties "no
        provider configured" to no key being set, not to any particular
        provider name.
        """
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached: `.env` is read once per process, not once per request."""
    return Settings()
