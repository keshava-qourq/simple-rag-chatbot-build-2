"""Config status router.

The one endpoint in the approved spec that belongs to no single pipeline
component: it just reports what `app.config.Settings` already knows, so the
UI can show the plain "no model configured" notice before a question is ever
sent (AC on api_backend / answer_gen).
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas import SUPPORTED_DOCUMENT_TYPES, ConfigStatusResponse

router = APIRouter(tags=["config"])


@router.get("/config/status", response_model=ConfigStatusResponse)
async def get_config_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConfigStatusResponse:
    """Report whether an answer-generation model is configured."""
    return ConfigStatusResponse(
        model_configured=settings.model_configured,
        supported_types=list(SUPPORTED_DOCUMENT_TYPES),
    )
