from pathlib import Path

from document_intelligence.security.posture import PostureStatus, evaluate_repository


def test_current_repository_passes_phase6_posture_checks() -> None:
    report = evaluate_repository(Path(__file__).resolve().parents[1])

    assert report.passed
    assert {check.id for check in report.checks} == {
        "secret-env-gitignore",
        "secret-env-tracked",
        "container-non-root",
        "container-no-env-copy",
        "llm-no-storage-tools",
        "answer-admission-control",
        "worker-malware-scanner",
        "compose-loopback-ports",
        "ci-no-pull-request-target",
        "phase6-docs",
        "phase6-drill-manifest",
    }


def test_posture_check_fails_when_dockerfile_has_no_user(tmp_path: Path) -> None:
    _write_minimal_repo(tmp_path, dockerfile="FROM python:3.12-slim\n")

    report = evaluate_repository(tmp_path, tracked_files=(".env.example",))
    container_check = next(check for check in report.checks if check.id == "container-non-root")

    assert not report.passed
    assert container_check.status == PostureStatus.FAIL
    assert container_check.detail == "Dockerfile has no USER directive"


def test_posture_check_fails_for_tracked_secret_env_file(tmp_path: Path) -> None:
    _write_minimal_repo(tmp_path)

    report = evaluate_repository(tmp_path, tracked_files=(".env", ".env.example"))
    env_check = next(check for check in report.checks if check.id == "secret-env-tracked")

    assert not report.passed
    assert env_check.status == PostureStatus.FAIL
    assert env_check.detail == "tracked environment files: .env"


def _write_minimal_repo(
    root: Path,
    *,
    dockerfile: str = "FROM python:3.12-slim\nUSER app\n",
) -> None:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / "src" / "document_intelligence" / "api" / "routes").mkdir(parents=True)
    (root / "src" / "document_intelligence" / "worker").mkdir(parents=True)
    (root / "src" / "document_intelligence" / "operations").mkdir(parents=True)
    (root / "src" / "document_intelligence" / "generation").mkdir(parents=True)
    (root / ".gitignore").write_text(".env\n.env.*\n!.env.example\n", encoding="utf-8")
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (root / "compose.yaml").write_text(
        'services:\n  api:\n    ports:\n      - "127.0.0.1:8000:8000"\n',
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "verify.yml").write_text(
        "on:\n  pull_request:\n  push:\n",
        encoding="utf-8",
    )
    (root / "src" / "document_intelligence" / "generation" / "openai.py").write_text(
        "client.responses.parse(store=False)\n",
        encoding="utf-8",
    )
    (root / "src" / "document_intelligence" / "api" / "routes" / "answers.py").write_text(
        "AnswerAdmissionController\nHTTP_429_TOO_MANY_REQUESTS\n",
        encoding="utf-8",
    )
    (root / "src" / "document_intelligence" / "config.py").write_text(
        "answer_rate_limit_per_minute\n"
        "answer_monthly_token_budget\n"
        "answer_estimated_output_tokens\n"
        "malware_scanner_command\n",
        encoding="utf-8",
    )
    (root / "src" / "document_intelligence" / "worker" / "composition.py").write_text(
        "ClamAVCommandScanner\nAPP_MALWARE_SCANNER_COMMAND\n",
        encoding="utf-8",
    )
    (root / "scripts" / "phase6_drill_manifest.py").write_text("main()\n", encoding="utf-8")
    (root / "src" / "document_intelligence" / "operations" / "drills.py").write_text(
        "phase6_drill_plans()\n",
        encoding="utf-8",
    )
    for doc in ("phase-6.md", "threat-model.md", "operations.md"):
        (root / "docs" / doc).write_text("# doc\n", encoding="utf-8")
