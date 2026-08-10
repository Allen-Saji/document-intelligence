from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from document_intelligence.parsing.picture_content import (
    PictureBoundingBox,
    PictureContentCandidate,
)


def test_pending_picture_content_cannot_enter_index() -> None:
    candidate = PictureContentCandidate(
        document_id="loc-wireless-telegraph-scan",
        page_number=11,
        picture_ref="#/pictures/0",
        text="EIGHT-WIRE CAGES",
        extraction_method="image-ocr",
    )

    assert candidate.is_indexable is False
    with pytest.raises(ValueError, match="accepted before indexing"):
        candidate.to_index_record()


def test_accepted_picture_content_uses_picture_channel() -> None:
    candidate = PictureContentCandidate(
        document_id="loc-wireless-telegraph-scan",
        page_number=11,
        picture_ref="#/pictures/0",
        text="EIGHT-WIRE CAGES",
        bbox=PictureBoundingBox(left=10, top=20, right=90, bottom=40),
        extraction_method="human-transcription",
        review_status="accepted",
        reviewed_by="allen",
        reviewed_at=datetime(2026, 8, 11, tzinfo=UTC),
        review_note="Verified against the rendered source page.",
    )

    record = candidate.to_index_record()

    assert record.index_channel == "picture"
    assert record.text == "EIGHT-WIRE CAGES"


def test_accepted_picture_content_requires_review_metadata() -> None:
    with pytest.raises(ValidationError, match="reviewer metadata"):
        PictureContentCandidate(
            document_id="doc",
            page_number=1,
            picture_ref="#/pictures/0",
            text="label",
            extraction_method="vlm",
            review_status="accepted",
        )


def test_rejected_picture_content_is_not_indexable() -> None:
    candidate = PictureContentCandidate(
        document_id="doc",
        page_number=1,
        picture_ref="#/pictures/0",
        text="unreliable text",
        extraction_method="image-ocr",
        review_status="rejected",
        reviewed_by="allen",
        reviewed_at=datetime(2026, 8, 11, tzinfo=UTC),
        review_note="OCR output is not reliable.",
    )

    assert candidate.is_indexable is False
