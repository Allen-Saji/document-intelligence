from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from document_intelligence.citations.contracts import EvidenceState, ModelAnswerDraft
from document_intelligence.generation.contracts import (
    CitationRepair,
    GenerationPrompt,
    PromptEvidence,
)
from document_intelligence.generation.openai import (
    OpenAIAnswerDraft,
    OpenAIResponsesProvider,
)

EVIDENCE_ID = "ev_00000000-0000-4000-8000-000000000005"


class Response:
    def __init__(self, output_parsed: object) -> None:
        self.output_parsed = output_parsed


class Responses:
    def __init__(self, response: Response | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class Client:
    def __init__(self, response: Response | Exception) -> None:
        self.responses = Responses(response)


def prompt() -> GenerationPrompt:
    return GenerationPrompt(
        question="What is required for finality?",
        evidence=(
            PromptEvidence(
                evidence_id=EVIDENCE_ID,
                content="Finality requires the voting threshold.",
            ),
        ),
    )


def draft() -> ModelAnswerDraft:
    return ModelAnswerDraft(
        state=EvidenceState.SUPPORTED,
        claims=(
            {
                "text": "Finality requires the voting threshold.",
                "evidence_ids": (EVIDENCE_ID,),
            },
        ),
    )


@pytest.mark.asyncio
async def test_openai_provider_uses_strict_structured_output_without_storage() -> None:
    client = Client(Response(OpenAIAnswerDraft.model_validate(draft().model_dump())))
    provider = OpenAIResponsesProvider(
        api_key=SecretStr("test-key"), model="gpt-5.6-luna", client=client
    )

    answer = await provider.generate(prompt())

    assert answer == draft()
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-luna"
    assert call["text_format"] is OpenAIAnswerDraft
    assert call["store"] is False
    body = json.loads(str(call["input"]))
    assert body == {
        "question": "What is required for finality?",
        "evidence": [
            {
                "evidence_id": EVIDENCE_ID,
                "content": "Finality requires the voting threshold.",
            }
        ],
    }


@pytest.mark.asyncio
async def test_openai_provider_sends_a_bounded_citation_repair_request() -> None:
    client = Client(Response(OpenAIAnswerDraft.model_validate(draft().model_dump())))
    provider = OpenAIResponsesProvider(
        api_key=SecretStr("test-key"), model="gpt-5.6-luna", client=client
    )
    correction = CitationRepair(allowed_evidence_ids=(EVIDENCE_ID,))

    await provider.repair(prompt(), draft(), correction)

    call = client.responses.calls[0]
    body = json.loads(str(call["input"]))
    assert body["citation_correction"] == {
        "failure": "citation_validation_failed",
        "allowed_evidence_ids": [EVIDENCE_ID],
    }
    assert "Correct the prior draft" in str(call["instructions"])


@pytest.mark.asyncio
async def test_openai_provider_rejects_missing_structured_output() -> None:
    client = Client(Response(None))
    provider = OpenAIResponsesProvider(
        api_key=SecretStr("test-key"), model="gpt-5.6-luna", client=client
    )

    with pytest.raises(RuntimeError, match="no structured answer"):
        await provider.generate(prompt())
