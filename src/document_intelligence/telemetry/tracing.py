from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

BLOCKED_ATTRIBUTE_PARTS = (
    "body",
    "content",
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
)


def safe_attributes(attributes: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    sanitized: dict[str, str | int | float | bool] = {}
    for key, value in attributes.items():
        normalized_key = key.casefold()
        if any(part in normalized_key for part in BLOCKED_ATTRIBUTE_PARTS):
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
    return sanitized


@contextmanager
def start_safe_span(
    tracer: trace.Tracer,
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[trace.Span]:
    with tracer.start_as_current_span(name) as span:
        if attributes:
            span.set_attributes(safe_attributes(attributes))
        yield span
