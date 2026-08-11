"""Shared immutable page geometry contracts."""

from pydantic import BaseModel, ConfigDict, model_validator


class PageRegion(BaseModel):
    """A source-page rectangle in the parser's native coordinate system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    left: float
    top: float
    right: float
    bottom: float

    @model_validator(mode="after")
    def has_positive_area(self) -> "PageRegion":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("page region must have positive area")
        return self


def enclosing_region(regions: tuple[PageRegion, ...]) -> PageRegion | None:
    if not regions:
        return None
    return PageRegion(
        left=min(region.left for region in regions),
        top=min(region.top for region in regions),
        right=max(region.right for region in regions),
        bottom=max(region.bottom for region in regions),
    )
