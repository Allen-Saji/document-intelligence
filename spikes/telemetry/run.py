from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from document_intelligence.telemetry.tracing import start_safe_span

STAGES = ("api", "workflow", "parser", "index", "query", "generation")


def run_probe(output: Path) -> dict[str, object]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "document-intelligence"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("document-intelligence.phase0")
    tenant_id = UUID("00000000-0000-4000-8000-000000000001")
    with start_safe_span(
        tracer,
        "document-intelligence.request",
        {
            "stage": "api",
            "organization_id": str(tenant_id),
            "document_body": "must-not-be-recorded",
            "api_key": "must-not-be-recorded",
        },
    ) as root:
        for stage in STAGES:
            with start_safe_span(
                tracer,
                f"document-intelligence.{stage}",
                {
                    "stage": stage,
                    "organization_id": str(tenant_id),
                    "document_id": "must-not-be-recorded",
                    "content": "must-not-be-recorded",
                    "latency_ms": 1.5,
                }
            ):
                pass
        root.set_attribute("answer_state", "supported")
    provider.force_flush()
    spans = exporter.get_finished_spans()
    trace_ids = {span.context.trace_id for span in spans}
    attributes = [attribute for span in spans for attribute in span.attributes]
    forbidden_attributes = [
        attribute
        for attribute in attributes
        if any(part in attribute.casefold() for part in ("body", "content", "api_key"))
    ]
    report: dict[str, object] = {
        "span_count": len(spans),
        "stage_names": sorted(span.name for span in spans),
        "trace_count": len(trace_ids),
        "forbidden_attributes": forbidden_attributes,
        "single_trace_verified": len(trace_ids) == 1,
        "redaction_verified": not forbidden_attributes,
    }
    report["telemetry_probe_verified"] = bool(
        report["single_trace_verified"]
        and report["redaction_verified"]
        and len(spans) == len(STAGES) + 1
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 0 telemetry propagation probe.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-0/telemetry/trace.json"),
    )
    args = parser.parse_args()
    report = run_probe(args.output)
    print(json.dumps(report, indent=2))
    if not report["telemetry_probe_verified"]:
        raise SystemExit("telemetry probe failed")


if __name__ == "__main__":
    main()
