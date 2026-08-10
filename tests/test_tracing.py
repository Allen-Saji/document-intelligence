from document_intelligence.telemetry.tracing import safe_attributes


def test_safe_attributes_drops_content_and_secret_fields() -> None:
    attributes = safe_attributes(
        {
            "organization_id": "org-1",
            "document_id": "doc-1",
            "document_body": "private body",
            "api_key": "private key",
            "latency_ms": 12.5,
        }
    )

    assert attributes == {
        "organization_id": "org-1",
        "document_id": "doc-1",
        "latency_ms": 12.5,
    }
