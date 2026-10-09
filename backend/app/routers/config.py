"""Config status router.

The one endpoint in the approved spec that belongs to no single pipeline
component: it just reports what `app.config.Settings` already knows, so the
UI can show whether the LLM and embedding providers are usable before a
question is ever sent (AC-062 through AC-067). It never echoes a key or key
fragment -- only booleans, model names and human-readable issue strings.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas import ConfigStatusResponse

router = APIRouter(tags=["config"])


@router.get("/config/status", response_model=ConfigStatusResponse)
async def get_config_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConfigStatusResponse:
    """Report whether the LLM and embedding providers are configured."""
    return ConfigStatusResponse(
        llm_configured=settings.llm_configured,
        embedding_configured=settings.embedding_configured,
        llm_model=settings.llm_chat_model,
        embedding_model=settings.embedding_model,
        issues=settings.issues,
    )
