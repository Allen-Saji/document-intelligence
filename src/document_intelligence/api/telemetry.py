from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from opentelemetry import trace

from document_intelligence.telemetry.tracing import start_safe_span

HttpHandler = Callable[[Request], Awaitable[Response]]


def install_request_telemetry(app: FastAPI) -> None:
    """Record content-free request spans without capturing credentials or request bodies."""

    tracer = trace.get_tracer("document_intelligence.api")

    @app.middleware("http")
    async def trace_request(request: Request, call_next: HttpHandler) -> Response:
        with start_safe_span(
            tracer,
            "http.request",
            {"http.method": request.method, "service.name": app.state.settings.service_name},
        ) as span:
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            return response
