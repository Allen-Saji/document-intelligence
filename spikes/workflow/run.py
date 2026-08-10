from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Final
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

TASK_QUEUE: Final = "document-intelligence-phase0"
TEMPORAL_TARGET: Final = "127.0.0.1:7234"


def _state_path() -> Path:
    return Path(os.environ["DI_WORKFLOW_STATE"])


def _marker_path(name: str) -> Path:
    return Path(os.environ[f"DI_WORKFLOW_{name}_MARKER"])


def _initialize_state(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_lifecycle (
                document_id TEXT PRIMARY KEY,
                parse_attempts INTEGER NOT NULL DEFAULT 0,
                indexed_attempts INTEGER NOT NULL DEFAULT 0,
                active_versions INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _record_parse(document_id: str) -> None:
    with sqlite3.connect(_state_path()) as connection:
        connection.execute(
            """
            INSERT INTO document_lifecycle(document_id, parse_attempts)
            VALUES (?, 1)
            ON CONFLICT(document_id) DO UPDATE SET parse_attempts = parse_attempts + 1
            """,
            (document_id,),
        )


def _record_index(document_id: str) -> None:
    with sqlite3.connect(_state_path()) as connection:
        connection.execute(
            """
            INSERT INTO document_lifecycle(document_id, indexed_attempts, active_versions)
            VALUES (?, 1, 1)
            ON CONFLICT(document_id) DO UPDATE SET
                indexed_attempts = indexed_attempts + 1,
                active_versions = 1
            """,
            (document_id,),
        )


@activity.defn
async def parse_document(document_id: str) -> str:
    _record_parse(document_id)
    _marker_path("PARSED").touch()
    return document_id


@activity.defn
async def index_document(document_id: str) -> str:
    if os.getenv("DI_KILL_AFTER_PARSE") == "1":
        _marker_path("INDEX_STARTED").touch()
        os._exit(42)
    _record_index(document_id)
    return document_id


@workflow.defn
class IngestionWorkflow:
    @workflow.run
    async def run(self, document_id: str) -> str:
        await workflow.execute_activity(
            parse_document,
            document_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return await workflow.execute_activity(
            index_document,
            document_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


async def worker_main(mode: str) -> None:
    client = await Client.connect(TEMPORAL_TARGET)
    activities = [parse_document, index_document]
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[IngestionWorkflow],
        activities=activities,
    )
    if mode == "kill-after-parse":
        os.environ["DI_KILL_AFTER_PARSE"] = "1"
    await worker.run()


def _spawn_worker(mode: str, state_dir: Path) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["DI_WORKFLOW_STATE"] = str(state_dir / "lifecycle.sqlite")
    environment["DI_WORKFLOW_PARSED_MARKER"] = str(state_dir / "parsed.marker")
    environment["DI_WORKFLOW_INDEX_STARTED_MARKER"] = str(state_dir / "index-started.marker")
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", "--mode", mode],
        env=environment,
        text=True,
    )


async def _wait_for_file(path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {path}")


def _read_state(path: Path, document_id: str) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT parse_attempts, indexed_attempts, active_versions
            FROM document_lifecycle WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("workflow did not persist lifecycle state")
    return {
        "parse_attempts": row[0],
        "indexed_attempts": row[1],
        "active_versions": row[2],
    }


async def run_probe(output: Path) -> dict[str, object]:
    state_dir = Path(mkdtemp(prefix="document-intelligence-workflow-"))
    state_path = state_dir / "lifecycle.sqlite"
    _initialize_state(state_path)
    document_id = f"document-{uuid4()}"
    workflow_id = f"phase0-recovery-{uuid4()}"
    client = await Client.connect(TEMPORAL_TARGET)

    crashing_worker = _spawn_worker("kill-after-parse", state_dir)
    await client.start_workflow(
        IngestionWorkflow.run,
        document_id,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await _wait_for_file(state_dir / "index-started.marker", 30)
    crashing_worker.wait(timeout=10)

    recovery_worker = _spawn_worker("full", state_dir)
    handle = client.get_workflow_handle(workflow_id)
    result = await handle.result()
    recovery_worker.terminate()
    recovery_worker.wait(timeout=10)

    state = _read_state(state_path, document_id)
    report: dict[str, object] = {
        "workflow_id": workflow_id,
        "document_id": document_id,
        "task_queue": TASK_QUEUE,
        "worker_killed_after_parse": True,
        "workflow_result": result,
        **state,
        "recovery_verified": state["parse_attempts"] == 1
        and state["indexed_attempts"] == 1
        and state["active_versions"] == 1,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 0 Temporal recovery probe.")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=["full", "kill-after-parse"], default="full")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-0/workflow/recovery.json"),
    )
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker_main(args.mode))
    else:
        report = asyncio.run(run_probe(args.output))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
