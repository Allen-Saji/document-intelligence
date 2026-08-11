from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from document_intelligence.api.app import create_app
from document_intelligence.auth.contracts import (
    ApiKeyPrincipal,
    ApiKeyScope,
    Membership,
    WorkspaceRole,
    issue_api_key,
)
from document_intelligence.config import Settings
from document_intelligence.core.tenancy import TenantContext
from document_intelligence.generation.service import AnswerStreamEvent

NOW = datetime(2026, 8, 11, tzinfo=UTC)
ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")
CORPUS_ID = UUID("00000000-0000-4000-8000-000000000004")
MEMBERSHIP = Membership(
    user_id=ACTOR_ID,
    organization_id=ORG_ID,
    workspace_id=WORKSPACE_ID,
    role=WorkspaceRole.MEMBER,
)


class Streamer:
    def __init__(self) -> None:
        self.calls: list[tuple[TenantContext, str, tuple[str, ...]]] = []

    async def stream(
        self,
        *,
        tenant: TenantContext,
        question: str,
        conversation: tuple[str, ...] = (),
    ) -> AsyncIterator[AnswerStreamEvent]:
        self.calls.append((tenant, question, tuple(conversation)))
        yield AnswerStreamEvent(event="status", data={"stage": "retrieval_started"})
        yield AnswerStreamEvent(
            event="answer",
            data={"state": "insufficient", "claims": [], "missing_information": ["missing"]},
        )


def build_client(
    *,
    scopes: tuple[ApiKeyScope, ...] = (ApiKeyScope.INVESTIGATION_READ,),
    with_orchestrator: bool = True,
    with_resolver: bool = True,
    readable_corpora: tuple[UUID, ...] = (CORPUS_ID,),
) -> tuple[httpx.AsyncClient, Streamer]:
    issued = issue_api_key(
        membership=MEMBERSHIP,
        label="answer test",
        requested_scopes=scopes,
        pepper="test-pepper",
        now=NOW,
    )
    streamer = Streamer()

    async def lookup(prefix: str):
        return issued.record if prefix == issued.record.token_prefix else None

    async def resolve(_: ApiKeyPrincipal) -> tuple[UUID, ...]:
        return readable_corpora

    app = create_app(Settings(env="test", api_key_pepper=SecretStr("test-pepper")))
    app.state.api_key_lookup = lookup
    if with_orchestrator:
        app.state.answer_orchestrator = streamer
    if with_resolver:
        app.state.corpus_access_resolver = resolve
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": issued.plaintext_token},
    )
    return client, streamer


@pytest.mark.asyncio
async def test_answer_stream_uses_authenticated_tenant_and_streams_sse() -> None:
    client, streamer = build_client()

    async with client:
        response = await client.post(
            "/v1/answers:stream",
            json={"question": "What is finality?", "conversation": ["Which spec?"]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "event: status" in response.text
    assert '"state":"insufficient"' in response.text
    tenant, question, conversation = streamer.calls[0]
    assert tenant.organization_id == ORG_ID
    assert tenant.workspace_id == WORKSPACE_ID
    assert tenant.actor_id == ACTOR_ID
    assert tenant.allowed_corpus_ids == (CORPUS_ID,)
    assert question == "What is finality?"
    assert conversation == ("Which spec?",)


@pytest.mark.asyncio
async def test_answer_stream_rejects_scope_and_evidence_from_client_payload() -> None:
    client, _ = build_client()

    async with client:
        response = await client.post(
            "/v1/answers:stream",
            json={
                "question": "What is finality?",
                "allowed_corpus_ids": [str(CORPUS_ID)],
                "evidence": [],
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_answer_stream_requires_investigation_read_scope() -> None:
    client, _ = build_client(scopes=(ApiKeyScope.DOCUMENT_READ,))

    async with client:
        response = await client.post("/v1/answers:stream", json={"question": "What is finality?"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_answer_stream_fails_closed_without_runtime_dependencies() -> None:
    missing_service_client, _ = build_client(with_orchestrator=False)
    missing_resolver_client, _ = build_client(with_resolver=False)

    async with missing_service_client:
        missing_service = await missing_service_client.post(
            "/v1/answers:stream", json={"question": "What is finality?"}
        )
    async with missing_resolver_client:
        missing_resolver = await missing_resolver_client.post(
            "/v1/answers:stream", json={"question": "What is finality?"}
        )

    assert missing_service.status_code == 503
    assert missing_resolver.status_code == 503


@pytest.mark.asyncio
async def test_answer_stream_rejects_callers_without_readable_corpora() -> None:
    client, streamer = build_client(readable_corpora=())

    async with client:
        response = await client.post("/v1/answers:stream", json={"question": "What is finality?"})

    assert response.status_code == 403
    assert response.json()["detail"] == "no readable corpora available"
    assert streamer.calls == []
