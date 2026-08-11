from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from document_intelligence.api.dependencies import require_investigation_read_principal
from document_intelligence.auth.contracts import ApiKeyPrincipal
from document_intelligence.core.tenancy import TenantContext
from document_intelligence.generation.orchestration import AnswerOrchestrator
from document_intelligence.security.limits import AdmissionRejectedError, AnswerAdmissionController

router = APIRouter(prefix="/v1", tags=["answers"])


class CorpusAccessResolver(Protocol):
    async def __call__(self, principal: ApiKeyPrincipal) -> tuple[UUID, ...]: ...


class AnswerQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=4_000)
    conversation: tuple[str, ...] = Field(default=(), max_length=20)


def _orchestrator(request: Request) -> AnswerOrchestrator:
    service: AnswerOrchestrator | None = getattr(request.app.state, "answer_orchestrator", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="answers unavailable"
        )
    return service


def _admission_controller(request: Request) -> AnswerAdmissionController | None:
    controller: AnswerAdmissionController | None = getattr(
        request.app.state, "answer_admission_controller", None
    )
    return controller


async def _tenant_context(request: Request, principal: ApiKeyPrincipal) -> TenantContext:
    resolver: Callable[[ApiKeyPrincipal], Awaitable[tuple[UUID, ...]]] | None = getattr(
        request.app.state, "corpus_access_resolver", None
    )
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="corpus authorization unavailable",
        )
    allowed_corpus_ids = await resolver(principal)
    try:
        return principal.tenant_context(tuple(allowed_corpus_ids))
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no readable corpora available",
        ) from error


@router.post(
    "/answers:stream",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Server-sent answer events",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_answer(
    payload: AnswerQuestionRequest,
    request: Request,
    principal: Annotated[ApiKeyPrincipal, Depends(require_investigation_read_principal)],
    orchestrator: Annotated[AnswerOrchestrator, Depends(_orchestrator)],
    admission_controller: Annotated[
        AnswerAdmissionController | None, Depends(_admission_controller)
    ],
) -> StreamingResponse:
    if admission_controller is not None:
        try:
            await admission_controller.admit(
                principal=principal,
                question=payload.question,
                conversation=payload.conversation,
            )
        except AdmissionRejectedError as error:
            headers = (
                {"Retry-After": str(error.retry_after_seconds)}
                if error.retry_after_seconds is not None
                else None
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=error.reason,
                headers=headers,
            ) from error
    tenant = await _tenant_context(request, principal)
    return StreamingResponse(
        (
            event.encode()
            async for event in orchestrator.stream(
                tenant=tenant,
                question=payload.question,
                conversation=payload.conversation,
            )
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
