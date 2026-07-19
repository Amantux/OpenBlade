"""API for the OpenBlade AI assistant (OpenAI-compatible helper agent)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from openblade.api.routes_aml_auth import require_auth
from openblade.assistant.client import AssistantClientError, OpenAICompatibleClient
from openblade.assistant.config import load_assistant_config
from openblade.assistant.service import AssistantService
from openblade.assistant.tools import tool_specs
from openblade.catalog.models import AmlUser

router = APIRouter()


def get_assistant_service(_: AmlUser = Depends(require_auth)) -> AssistantService:
    """Build the assistant service, or 503 if the endpoint is not configured.

    Depends on auth so unauthenticated calls get 401 before any 503. Overridable
    in tests via ``app.dependency_overrides``.
    """
    config = load_assistant_config()
    if not config.enabled or not config.configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI assistant is not configured. Set OPENBLADE_ASSISTANT_BASE_URL and "
                "OPENBLADE_ASSISTANT_MODEL (and OPENBLADE_ASSISTANT_API_KEY for hosted "
                "endpoints), or the standard OPENAI_* variables."
            ),
        )
    client = OpenAICompatibleClient(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
    )
    return AssistantService(config, client)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    model: str


class AssistantStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    model: str
    endpoint: str
    tools: list[str]


@router.get("/status", response_model=AssistantStatusResponse)
async def assistant_status(_: AmlUser = Depends(require_auth)) -> AssistantStatusResponse:
    config = load_assistant_config()
    return AssistantStatusResponse(
        enabled=config.enabled,
        configured=config.configured,
        model=config.model if config.configured else "",
        endpoint=config.base_host if config.configured else "",
        tools=[spec["function"]["name"] for spec in tool_specs()],
    )


@router.post("/chat", response_model=ChatResponse)
async def assistant_chat(
    request: ChatRequest,
    service: AssistantService = Depends(get_assistant_service),
) -> ChatResponse:
    try:
        reply = service.chat([message.model_dump() for message in request.messages])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AssistantClientError as error:
        raise HTTPException(
            status_code=502, detail=f"Assistant endpoint error: {error}"
        ) from error
    return ChatResponse(reply=reply.content, tools_used=reply.tools_used, model=reply.model)
