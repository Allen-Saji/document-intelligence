from __future__ import annotations

import asyncio

from document_intelligence.config import get_settings
from document_intelligence.worker.composition import build_worker_runtime, create_temporal_worker


async def main() -> None:
    settings = get_settings()
    runtime = build_worker_runtime(settings)
    try:
        worker = await create_temporal_worker(settings, runtime)
        await worker.run()
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
