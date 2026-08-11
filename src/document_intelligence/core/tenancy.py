from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatabaseTenantContext(BaseModel):
    """Verified server-side database scope independent of corpus authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    workspace_id: UUID
    actor_id: UUID


class TenantContext(DatabaseTenantContext):
    """Verified server-side tenant context including a non-empty corpus authorization set.

    This object must be derived from an authenticated session or API key. Request payloads must
    never be allowed to construct it directly.
    """

    allowed_corpus_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def corpus_ids_are_unique(self) -> "TenantContext":
        if len(set(self.allowed_corpus_ids)) != len(self.allowed_corpus_ids):
            raise ValueError("allowed_corpus_ids must not contain duplicates")
        return self
