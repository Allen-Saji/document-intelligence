"""Write the checked-in OpenAPI contract without needing a running server."""

from __future__ import annotations

import json
from pathlib import Path

from document_intelligence.api.app import create_app
from document_intelligence.config import Settings


def main() -> None:
    output = Path("docs/openapi.json")
    output.write_text(
        json.dumps(create_app(Settings(env="test")).openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
