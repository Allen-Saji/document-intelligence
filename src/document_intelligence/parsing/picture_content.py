from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PictureBoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: float
    top: float
    right: float
    bottom: float

    @model_validator(mode="after")
    def require_area(self) -> PictureBoundingBox:
        if self.left >= self.right:
            raise ValueError("picture bounding box must have positive width")
        if self.top == self.bottom:
            raise ValueError("picture bounding box must have positive height")
        return self


class PictureIndexRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    picture_ref: str = Field(pattern=r"^#/pictures/[0-9]+$")
    text: str = Field(min_length=1)
    bbox: PictureBoundingBox | None = None
    extraction_method: Literal["docling-picture-text", "image-ocr", "vlm", "human-transcription"]
    index_channel: Literal["picture"] = "picture"


class PictureContentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    picture_ref: str = Field(pattern=r"^#/pictures/[0-9]+$")
    text: str | None = Field(default=None, min_length=1)
    bbox: PictureBoundingBox | None = None
    extraction_method: Literal["docling-picture-text", "image-ocr", "vlm", "human-transcription"]
    review_status: Literal["pending", "accepted", "rejected"] = "pending"
    reviewed_by: str | None = Field(default=None, min_length=1)
    reviewed_at: datetime | None = None
    review_note: str | None = Field(default=None, min_length=1)
    index_channel: Literal["picture"] = "picture"

    @model_validator(mode="after")
    def validate_review(self) -> PictureContentCandidate:
        if self.review_status == "accepted":
            if self.text is None:
                raise ValueError("accepted picture content requires text")
            if self.reviewed_by is None or self.reviewed_at is None:
                raise ValueError("accepted picture content requires reviewer metadata")
        if self.review_status != "pending" and (
            self.reviewed_by is None or self.reviewed_at is None
        ):
            raise ValueError("reviewed picture content requires reviewer metadata")
        return self

    @property
    def is_indexable(self) -> bool:
        return self.review_status == "accepted" and self.text is not None

    def to_index_record(self) -> PictureIndexRecord:
        if not self.is_indexable:
            raise ValueError("picture content must be accepted before indexing")
        assert self.text is not None
        return PictureIndexRecord(
            document_id=self.document_id,
            page_number=self.page_number,
            picture_ref=self.picture_ref,
            text=self.text,
            bbox=self.bbox,
            extraction_method=self.extraction_method,
        )
