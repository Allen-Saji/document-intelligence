from __future__ import annotations

import argparse
import sys
from pathlib import Path

from document_intelligence.security.posture import evaluate_repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local Phase 6 security posture checks for Document Intelligence."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to inspect.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = evaluate_repository(args.repo)
    if args.json:
        print(report.to_json())
    else:
        for check in report.checks:
            print(f"{check.status.value.upper()} {check.id}: {check.detail}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
