from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from document_intelligence.auth.contracts import ApiKeyPrincipal


class AdmissionRejectedError(Exception):
    def __init__(self, *, reason: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class AdmissionDecision:
    estimated_tokens: int


@dataclass(frozen=True)
class TenantUsageKey:
    organization_id: UUID
    workspace_id: UUID
    actor_id: UUID

    @classmethod
    def from_principal(cls, principal: ApiKeyPrincipal) -> TenantUsageKey:
        return cls(
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
        )


class AnswerAdmissionController:
    """Bound answer requests before retrieval and generation spend can start."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        monthly_token_budget: int,
        estimated_output_tokens: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        if monthly_token_budget < 1:
            raise ValueError("monthly_token_budget must be positive")
        if estimated_output_tokens < 1:
            raise ValueError("estimated_output_tokens must be positive")
        self._requests_per_minute = requests_per_minute
        self._monthly_token_budget = monthly_token_budget
        self._estimated_output_tokens = estimated_output_tokens
        self._clock = clock or (lambda: datetime.now(UTC))
        self._request_windows: dict[TenantUsageKey, tuple[datetime, int]] = {}
        self._monthly_usage: dict[tuple[TenantUsageKey, str], int] = defaultdict(int)

    async def admit(
        self,
        *,
        principal: ApiKeyPrincipal,
        question: str,
        conversation: Sequence[str],
    ) -> AdmissionDecision:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("admission clock must return timezone-aware datetimes")
        key = TenantUsageKey.from_principal(principal)
        self._check_rate(key, now)
        estimated_tokens = estimate_answer_tokens(
            question=question,
            conversation=conversation,
            estimated_output_tokens=self._estimated_output_tokens,
        )
        self._reserve_budget(key, now, estimated_tokens)
        return AdmissionDecision(estimated_tokens=estimated_tokens)

    def _check_rate(self, key: TenantUsageKey, now: datetime) -> None:
        window_start, count = self._request_windows.get(key, (now, 0))
        elapsed = now - window_start
        if elapsed >= timedelta(minutes=1):
            self._request_windows[key] = (now, 1)
            return
        if count >= self._requests_per_minute:
            retry_after = max(1, int((timedelta(minutes=1) - elapsed).total_seconds()))
            raise AdmissionRejectedError(
                reason="answer rate limit exceeded",
                retry_after_seconds=retry_after,
            )
        self._request_windows[key] = (window_start, count + 1)

    def _reserve_budget(self, key: TenantUsageKey, now: datetime, estimated_tokens: int) -> None:
        month = f"{now.year:04d}-{now.month:02d}"
        usage_key = (key, month)
        next_total = self._monthly_usage[usage_key] + estimated_tokens
        if next_total > self._monthly_token_budget:
            raise AdmissionRejectedError(
                reason="answer token budget exceeded",
                retry_after_seconds=None,
            )
        self._monthly_usage[usage_key] = next_total


def estimate_answer_tokens(
    *,
    question: str,
    conversation: Sequence[str],
    estimated_output_tokens: int,
) -> int:
    # Conservative local estimate for proof gates. Production can replace this with
    # provider-reported usage accounting once answer persistence exists.
    input_characters = len(question) + sum(len(turn) for turn in conversation)
    estimated_input_tokens = max(1, (input_characters + 3) // 4)
    return estimated_input_tokens + estimated_output_tokens
