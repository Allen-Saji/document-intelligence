from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from document_intelligence.auth.contracts import ApiKeyPrincipal, ApiKeyScope
from document_intelligence.security.limits import (
    AdmissionRejectedError,
    AnswerAdmissionController,
    estimate_answer_tokens,
)

ORG_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"
ACTOR_ID = "00000000-0000-4000-8000-000000000003"


def principal() -> ApiKeyPrincipal:
    return ApiKeyPrincipal(
        api_key_id=UUID("00000000-0000-4000-8000-000000000004"),
        organization_id=UUID(ORG_ID),
        workspace_id=UUID(WORKSPACE_ID),
        actor_id=UUID(ACTOR_ID),
        scopes=(ApiKeyScope.INVESTIGATION_READ,),
    )


@pytest.mark.asyncio
async def test_answer_admission_rate_limits_by_actor_window() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    controller = AnswerAdmissionController(
        requests_per_minute=1,
        monthly_token_budget=10_000,
        estimated_output_tokens=100,
        clock=lambda: now,
    )

    await controller.admit(principal=principal(), question="one", conversation=())

    with pytest.raises(AdmissionRejectedError) as error:
        await controller.admit(principal=principal(), question="two", conversation=())

    assert error.value.reason == "answer rate limit exceeded"
    assert error.value.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_answer_admission_window_resets() -> None:
    current = datetime(2026, 8, 11, tzinfo=UTC)

    def clock() -> datetime:
        return current

    controller = AnswerAdmissionController(
        requests_per_minute=1,
        monthly_token_budget=10_000,
        estimated_output_tokens=100,
        clock=clock,
    )

    await controller.admit(principal=principal(), question="one", conversation=())
    current = current + timedelta(minutes=1)
    decision = await controller.admit(principal=principal(), question="two", conversation=())

    assert decision.estimated_tokens == 101


@pytest.mark.asyncio
async def test_answer_admission_rejects_monthly_budget_overage() -> None:
    controller = AnswerAdmissionController(
        requests_per_minute=10,
        monthly_token_budget=105,
        estimated_output_tokens=100,
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )

    await controller.admit(principal=principal(), question="abcd", conversation=())

    with pytest.raises(AdmissionRejectedError) as error:
        await controller.admit(principal=principal(), question="abcd", conversation=())

    assert error.value.reason == "answer token budget exceeded"
    assert error.value.retry_after_seconds is None


def test_answer_token_estimate_includes_conversation_and_output_budget() -> None:
    assert (
        estimate_answer_tokens(
            question="abcd",
            conversation=("abcd", "abcdefgh"),
            estimated_output_tokens=20,
        )
        == 24
    )
