import re
from uuid import UUID

SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def original_pdf_key(
    *,
    organization_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    version_id: UUID,
    sha256: str,
) -> str:
    """Create a generated, tenant-scoped key for an immutable original PDF."""

    normalized_sha256 = sha256.lower()
    if not SHA256_PATTERN.fullmatch(normalized_sha256):
        raise ValueError("sha256 must contain exactly 64 lowercase hexadecimal characters")

    return "/".join(
        (
            "organizations",
            str(organization_id),
            "workspaces",
            str(workspace_id),
            "documents",
            str(document_id),
            "versions",
            str(version_id),
            f"original-{normalized_sha256}.pdf",
        )
    )
