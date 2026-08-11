from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from document_intelligence.provenance import PageRegion


class IngestionStage(StrEnum):
    SCANNED = "scanned"
    PARSED = "parsed"
    QUARANTINED = "quarantined"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    PUBLISHED = "published"
    FAILED = "failed"


class SourceDocument(BaseModel):
    """Verified immutable source metadata, never a user-controlled filename or URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: UUID
    object_key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(gt=0)
    content_type: str = "application/pdf"

    @model_validator(mode="after")
    def require_pdf(self) -> SourceDocument:
        if self.content_type != "application/pdf":
            raise ValueError("only verified PDFs may enter ingestion")
        return self


class PageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    text: str = ""
    source_region: PageRegion | None = None
    quality_reasons: tuple[str, ...] = ()

    @property
    def searchable(self) -> bool:
        return bool(self.text.strip()) and not self.quality_reasons


class IngestionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SourceDocument
    pages: tuple[PageExtraction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_page_numbers(self) -> IngestionDocument:
        page_numbers = [page.page_number for page in self.pages]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("ingestion pages must be unique")
        return self

    @property
    def quarantined_pages(self) -> tuple[PageExtraction, ...]:
        return tuple(page for page in self.pages if not page.searchable)

    @property
    def searchable_pages(self) -> tuple[PageExtraction, ...]:
        return tuple(page for page in self.pages if page.searchable)


class ProcessingOutcome(BaseModel):
    """Terminal, content-free workflow result used for lifecycle and operator state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: IngestionStage
    searchable_page_count: int = Field(ge=0)
    quarantined_page_count: int = Field(ge=0)
    failure_code: str | None = None

    @model_validator(mode="after")
    def terminal_state_matches_result(self) -> ProcessingOutcome:
        if self.stage == IngestionStage.FAILED and self.failure_code is None:
            raise ValueError("failed processing requires a failure code")
        if self.stage != IngestionStage.FAILED and self.failure_code is not None:
            raise ValueError("only failed processing may include a failure code")
        if self.stage == IngestionStage.PUBLISHED and self.searchable_page_count == 0:
            raise ValueError("published documents require searchable pages")
        return self
