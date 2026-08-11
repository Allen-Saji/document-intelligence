"""Run one synthetic, secret-safe OpenAI structured-generation smoke request."""

import asyncio

from document_intelligence.config import Settings
from document_intelligence.generation.contracts import GenerationPrompt, PromptEvidence
from document_intelligence.generation.openai import OpenAIResponsesProvider


async def main() -> None:
    settings = Settings(env="test")
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise RuntimeError("OpenAI API key is not available through environment configuration")

    evidence_id = "ev_00000000-0000-4000-8000-000000000005"
    provider = OpenAIResponsesProvider(
        api_key=settings.openai_api_key,
        model=settings.generation_test_model,
    )
    answer = await provider.generate(
        GenerationPrompt(
            question="What voting count reaches finality?",
            evidence=(
                PromptEvidence(
                    evidence_id=evidence_id,
                    content="Finality is reached after two votes.",
                ),
            ),
        )
    )
    citation_matches = answer.claims and answer.claims[0].evidence_ids == (evidence_id,)
    print(f"state={answer.state}")
    print(f"citation_matches={bool(citation_matches)}")


if __name__ == "__main__":
    asyncio.run(main())
