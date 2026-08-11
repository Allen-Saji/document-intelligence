from __future__ import annotations

import hashlib
from uuid import UUID


def tenant_cache_key(
    *, organization_id: UUID, workspace_id: UUID, namespace: str, value: str
) -> str:
    """Construct a bounded cache key that cannot collide across tenant boundaries."""

    if not namespace or not value:
        raise ValueError("cache namespace and value must not be empty")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"di:{organization_id}:{workspace_id}:{namespace}:{digest}"
