from __future__ import annotations

import json
from typing import Any, Protocol, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, SecretStr

from document_intelligence.citations.contracts import ClaimDraft, EvidenceState, ModelAnswerDraft
from document_intelligence.generation.contracts import (
    CitationRepair,
    GenerationPrompt,
    GenerationProviderError,
)


class OpenAIAnswerDraft(BaseModel):
    """All fields are required because OpenAI strict structured outputs require that shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: EvidenceState
    claims: tuple[ClaimDraft, ...]
    missing_information: tuple[str, ...]


class ResponsesResource(Protocol):
    async def parse(self, **kwargs: Any) -> object: ...


class OpenAIClient(Protocol):
    @property
    def responses(self) -> ResponsesResource: ...


class OpenAIResponsesProvider:
    """Responses API adapter using strict structured output and no provider-side storage."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        client: OpenAIClient | None = None,
    ) -> None:
        if not model:
            raise ValueError("OpenAI model must not be empty")
        if not api_key.get_secret_value():
            raise ValueError("OpenAI API key must not be empty")
        self._model = model
        self._client = client or cast(
            OpenAIClient,
            AsyncOpenAI(api_key=api_key.get_secret_value(), max_retries=0, timeout=20.0),
        )

    async def generate(self, prompt: GenerationPrompt) -> ModelAnswerDraft:
        return await self._parse(prompt)

    async def repair(
        self,
        prompt: GenerationPrompt,
        draft: ModelAnswerDraft,
        correction: CitationRepair,
    ) -> ModelAnswerDraft:
        return await self._parse(prompt, draft=draft, correction=correction)

    async def _parse(
        self,
        prompt: GenerationPrompt,
        *,
        draft: ModelAnswerDraft | None = None,
        correction: CitationRepair | None = None,
    ) -> ModelAnswerDraft:
        instructions = list(prompt.instructions)
        if correction is not None:
            instructions.append(
                "Correct the prior draft using only the allowed opaque evidence IDs. "
                "Do not add new claims unless they are cited."
            )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions="\n".join(instructions),
                input=_input(prompt, draft=draft, correction=correction),
                text_format=OpenAIAnswerDraft,
                max_output_tokens=1_200,
                store=False,
            )
        except Exception as error:
            raise GenerationProviderError("OpenAI generation request failed") from error

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise GenerationProviderError("OpenAI generation returned no structured answer")
        try:
            return ModelAnswerDraft.model_validate(
                parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
            )
        except Exception as error:
            raise GenerationProviderError("OpenAI generation returned an invalid answer") from error


def _input(
    prompt: GenerationPrompt,
    *,
    draft: ModelAnswerDraft | None,
    correction: CitationRepair | None,
) -> str:
    payload: dict[str, object] = {
        "question": prompt.question,
        "evidence": [item.model_dump() for item in prompt.evidence],
    }
    if draft is not None and correction is not None:
        payload["previous_draft"] = draft.model_dump(mode="json")
        payload["citation_correction"] = correction.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
