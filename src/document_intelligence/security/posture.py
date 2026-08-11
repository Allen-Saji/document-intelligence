from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PostureStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class PostureCheck:
    id: str
    category: str
    title: str
    status: PostureStatus
    detail: str


@dataclass(frozen=True)
class SecurityPostureReport:
    checks: tuple[PostureCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.status == PostureStatus.PASS for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "id": check.id,
                    "category": check.category,
                    "title": check.title,
                    "status": check.status.value,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def evaluate_repository(
    root: Path, *, tracked_files: Iterable[str] | None = None
) -> SecurityPostureReport:
    """Evaluate local security posture gates that do not require deployment.

    These checks are intentionally narrow. They prove that the repository preserves the
    current security boundary: secrets stay out of Git, containers avoid root execution,
    AI calls avoid provider-side storage and tools, local compose ports stay loopback-only,
    and Phase 6 operator documentation exists.
    """

    repo = root.resolve()
    checks = (
        _gitignore_blocks_env(repo),
        _tracked_env_files_are_templates(repo, tracked_files=tracked_files),
        _dockerfile_uses_non_root_user(repo),
        _dockerfile_does_not_copy_env(repo),
        _openai_adapter_disables_storage_and_tools(repo),
        _answer_path_has_admission_control(repo),
        _worker_requires_malware_scanner(repo),
        _compose_ports_are_loopback(repo),
        _github_actions_avoid_unsafe_pr_target(repo),
        _phase6_docs_exist(repo),
        _phase6_drill_manifest_exists(repo),
    )
    return SecurityPostureReport(checks=checks)


def default_tracked_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _pass(check_id: str, category: str, title: str, detail: str) -> PostureCheck:
    return PostureCheck(
        id=check_id,
        category=category,
        title=title,
        status=PostureStatus.PASS,
        detail=detail,
    )


def _fail(check_id: str, category: str, title: str, detail: str) -> PostureCheck:
    return PostureCheck(
        id=check_id,
        category=category,
        title=title,
        status=PostureStatus.FAIL,
        detail=detail,
    )


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _gitignore_blocks_env(root: Path) -> PostureCheck:
    text = _read(root, ".gitignore")
    required = {".env", ".env.*", "!.env.example"}
    present = {line.strip() for line in text.splitlines()}
    missing = sorted(required - present)
    if missing:
        return _fail(
            "secret-env-gitignore",
            "secrets",
            "Environment files are ignored except the template",
            f"missing entries: {', '.join(missing)}",
        )
    return _pass(
        "secret-env-gitignore",
        "secrets",
        "Environment files are ignored except the template",
        ".env and .env.* are ignored; .env.example remains tracked",
    )


def _tracked_env_files_are_templates(
    root: Path, *, tracked_files: Iterable[str] | None
) -> PostureCheck:
    files = tuple(tracked_files) if tracked_files is not None else default_tracked_files(root)
    unsafe = sorted(
        file
        for file in files
        if (Path(file).name.startswith(".env") or file.endswith(".env"))
        and Path(file).name != ".env.example"
    )
    if unsafe:
        return _fail(
            "secret-env-tracked",
            "secrets",
            "Only environment templates are tracked",
            f"tracked environment files: {', '.join(unsafe)}",
        )
    return _pass(
        "secret-env-tracked",
        "secrets",
        "Only environment templates are tracked",
        "no tracked .env files except .env.example",
    )


def _dockerfile_uses_non_root_user(root: Path) -> PostureCheck:
    text = _read(root, "Dockerfile")
    users = [
        line.split(maxsplit=1)[1].strip()
        for line in text.splitlines()
        if line.strip().upper().startswith("USER ")
    ]
    if not users:
        return _fail(
            "container-non-root",
            "container",
            "Runtime container uses a non-root user",
            "Dockerfile has no USER directive",
        )
    final_user = users[-1]
    if final_user in {"0", "root"}:
        return _fail(
            "container-non-root",
            "container",
            "Runtime container uses a non-root user",
            f"final USER is {final_user}",
        )
    return _pass(
        "container-non-root",
        "container",
        "Runtime container uses a non-root user",
        f"final USER is {final_user}",
    )


def _dockerfile_does_not_copy_env(root: Path) -> PostureCheck:
    text = _read(root, "Dockerfile")
    risky = [
        line.strip()
        for line in text.splitlines()
        if line.strip().upper().startswith(("COPY ", "ADD ")) and ".env" in line
    ]
    if risky:
        return _fail(
            "container-no-env-copy",
            "container",
            "Runtime image does not copy environment files",
            f"risky instructions: {'; '.join(risky)}",
        )
    return _pass(
        "container-no-env-copy",
        "container",
        "Runtime image does not copy environment files",
        "Dockerfile has no COPY or ADD instruction for .env files",
    )


def _openai_adapter_disables_storage_and_tools(root: Path) -> PostureCheck:
    text = _read(root, "src/document_intelligence/generation/openai.py")
    has_store_false = "store=False" in text
    uses_tools = "tools=" in text or "tool_choice=" in text
    if not has_store_false or uses_tools:
        problems: list[str] = []
        if not has_store_false:
            problems.append("missing store=False")
        if uses_tools:
            problems.append("tool use is configured")
        return _fail(
            "llm-no-storage-tools",
            "llm",
            "OpenAI adapter disables provider storage and tools",
            ", ".join(problems),
        )
    return _pass(
        "llm-no-storage-tools",
        "llm",
        "OpenAI adapter disables provider storage and tools",
        "Responses calls set store=False and do not configure tools",
    )


def _answer_path_has_admission_control(root: Path) -> PostureCheck:
    route = _read(root, "src/document_intelligence/api/routes/answers.py")
    settings = _read(root, "src/document_intelligence/config.py")
    if "AnswerAdmissionController" not in route or "HTTP_429_TOO_MANY_REQUESTS" not in route:
        return _fail(
            "answer-admission-control",
            "llm",
            "Answer path has rate and spend admission control",
            "answer route does not enforce admission before orchestration",
        )
    required_settings = (
        "answer_rate_limit_per_minute",
        "answer_monthly_token_budget",
        "answer_estimated_output_tokens",
    )
    missing = [name for name in required_settings if name not in settings]
    if missing:
        return _fail(
            "answer-admission-control",
            "llm",
            "Answer path has rate and spend admission control",
            f"missing settings: {', '.join(missing)}",
        )
    return _pass(
        "answer-admission-control",
        "llm",
        "Answer path has rate and spend admission control",
        "answer route can return 429 before retrieval or generation starts",
    )


def _worker_requires_malware_scanner(root: Path) -> PostureCheck:
    worker = _read(root, "src/document_intelligence/worker/composition.py")
    settings = _read(root, "src/document_intelligence/config.py")
    if "ClamAVCommandScanner" not in worker or "APP_MALWARE_SCANNER_COMMAND" not in worker:
        return _fail(
            "worker-malware-scanner",
            "ingestion",
            "Worker requires external malware scanning",
            "worker composition does not require ClamAVCommandScanner",
        )
    if "malware_scanner_command" not in settings:
        return _fail(
            "worker-malware-scanner",
            "ingestion",
            "Worker requires external malware scanning",
            "settings do not include malware_scanner_command",
        )
    return _pass(
        "worker-malware-scanner",
        "ingestion",
        "Worker requires external malware scanning",
        "worker composition chains integrity verification and ClamAV-compatible scanning",
    )


def _compose_ports_are_loopback(root: Path) -> PostureCheck:
    text = _read(root, "compose.yaml")
    unsafe_ports = [
        line.strip()
        for line in text.splitlines()
        if line.lstrip().startswith("- ")
        and ":" in line
        and line.strip().startswith('- "')
        and not line.strip().startswith('- "127.0.0.1:')
    ]
    if unsafe_ports:
        return _fail(
            "compose-loopback-ports",
            "infrastructure",
            "Compose host ports bind to loopback",
            f"non-loopback port mappings: {'; '.join(unsafe_ports)}",
        )
    return _pass(
        "compose-loopback-ports",
        "infrastructure",
        "Compose host ports bind to loopback",
        "all quoted Compose port mappings bind to 127.0.0.1",
    )


def _github_actions_avoid_unsafe_pr_target(root: Path) -> PostureCheck:
    workflow_dir = root / ".github" / "workflows"
    workflows = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    offenders = [
        str(path.relative_to(root))
        for path in workflows
        if "pull_request_target" in path.read_text(encoding="utf-8")
    ]
    if offenders:
        return _fail(
            "ci-no-pull-request-target",
            "ci",
            "CI avoids pull_request_target",
            f"unsafe trigger in: {', '.join(offenders)}",
        )
    return _pass(
        "ci-no-pull-request-target",
        "ci",
        "CI avoids pull_request_target",
        f"checked {len(workflows)} workflow file(s)",
    )


def _phase6_docs_exist(root: Path) -> PostureCheck:
    required = ("docs/phase-6.md", "docs/threat-model.md", "docs/operations.md")
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        return _fail(
            "phase6-docs",
            "operations",
            "Phase 6 security and operations docs exist",
            f"missing docs: {', '.join(missing)}",
        )
    return _pass(
        "phase6-docs",
        "operations",
        "Phase 6 security and operations docs exist",
        f"found: {', '.join(required)}",
    )


def _phase6_drill_manifest_exists(root: Path) -> PostureCheck:
    required = (
        "scripts/phase6_drill_manifest.py",
        "src/document_intelligence/operations/drills.py",
    )
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        return _fail(
            "phase6-drill-manifest",
            "operations",
            "Phase 6 operational drill manifest exists",
            f"missing files: {', '.join(missing)}",
        )
    return _pass(
        "phase6-drill-manifest",
        "operations",
        "Phase 6 operational drill manifest exists",
        f"found: {', '.join(required)}",
    )
