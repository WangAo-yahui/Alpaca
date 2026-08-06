#!/usr/bin/env python3
"""Export a deduplicated, credential-checked live1 evidence snapshot.

The runtime tree stays ignored by Git.  This tool copies only operational logs
and the user-facing natural-language daily reports into a dated evidence tree.
It refuses to export any file containing an exact secret from ``.env_live``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from v2.deployment.redaction import dotenv_secret_values  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_bytes(path: Path, secrets: tuple[str, ...]) -> bytes:
    data = path.read_bytes()
    for secret in secrets:
        if secret and secret.encode() in data:
            raise RuntimeError(
                f"refusing to export file containing a live secret: {path}"
            )
    return data


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def export_snapshot(snapshot_date: str) -> Path:
    source_logs = PROJECT_ROOT / "var" / "shared" / "logs" / "live1"
    source_reports = PROJECT_ROOT / "natural_language"
    destination = PROJECT_ROOT / "evidence" / "live1" / snapshot_date
    if destination.exists():
        raise RuntimeError(f"snapshot already exists: {destination}")
    if not source_logs.is_dir() or not source_reports.is_dir():
        raise RuntimeError("live1 logs or natural-language report index is missing")

    secrets = dotenv_secret_values(PROJECT_ROOT / ".env_live")
    log_destination = destination / "logs"
    report_destination = destination / "reports" / "natural_language"
    log_destination.mkdir(parents=True)
    report_destination.mkdir(parents=True)

    unique_logs: dict[str, str] = {}
    log_files: list[dict[str, str]] = []
    duplicate_logs: list[dict[str, str]] = []
    for source in sorted(path for path in source_logs.rglob("*") if path.is_file()):
        relative = source.relative_to(source_logs)
        data = _checked_bytes(source, secrets)
        digest = _sha256(data)
        if digest in unique_logs:
            duplicate_logs.append(
                {
                    "source": relative.as_posix(),
                    "same_content_as": unique_logs[digest],
                    "sha256": digest,
                }
            )
            continue
        target = log_destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        stored = target.relative_to(destination).as_posix()
        unique_logs[digest] = stored
        log_files.append(
            {
                "source": relative.as_posix(),
                "stored": stored,
                "sha256": digest,
            }
        )

    report_files: list[dict[str, str]] = []
    seen_reports: dict[str, str] = {}
    for source in sorted(source_reports.glob("*.md")):
        if source.name == "latest.md":
            continue
        data = _checked_bytes(source.resolve(), secrets)
        digest = _sha256(data)
        if digest in seen_reports:
            raise RuntimeError(
                f"duplicate daily report content: {source.name} and {seen_reports[digest]}"
            )
        target = report_destination / source.name
        target.write_bytes(data)
        seen_reports[digest] = source.name
        report_files.append(
            {
                "source_index": source.name,
                "stored": target.relative_to(destination).as_posix(),
                "sha256": digest,
            }
        )

    latest_link = source_reports / "latest.md"
    latest_report = latest_link.resolve().name if latest_link.exists() else None
    manifest = {
        "schema_version": 1,
        "scope": "live1",
        "snapshot_date": snapshot_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_git_commit": _git_head(),
        "credential_check": {
            "dotenv": ".env_live",
            "secret_values_checked": len(secrets),
            "exact_secret_matches": 0,
        },
        "logs": {
            "source_file_count": len(log_files) + len(duplicate_logs),
            "stored_unique_count": len(log_files),
            "files": log_files,
            "duplicate_aliases": duplicate_logs,
        },
        "daily_reports": {
            "stored_count": len(report_files),
            "latest": latest_report,
            "files": report_files,
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    readme = f"""# live1 operational evidence — {snapshot_date}

This private snapshot contains live1 operational logs and the most complete
natural-language daily report for each available trading date.

- Logs: {len(log_files)} unique files retained from {len(log_files) + len(duplicate_logs)} source files.
- Exact-content duplicate logs omitted: {len(duplicate_logs)}.
- Complete daily reports: {len(report_files)}; latest report: `{latest_report}`.
- Credential scan: {len(secrets)} values from `.env_live` checked, zero exact matches.

The snapshot intentionally excludes `.env_live`, account bindings, market-data
caches, release/runtime state, locks, and any other broker-writable artifacts.
Financial positions and account-value facts may remain in reports because this
repository is private and those facts are part of the requested daily evidence.
See `manifest.json` for SHA-256 checksums and duplicate-log aliases.
"""
    (destination / "README.md").write_text(readme, encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot-date",
        required=True,
        help="Destination date in YYYY-MM-DD format",
    )
    args = parser.parse_args()
    datetime.strptime(args.snapshot_date, "%Y-%m-%d")
    destination = export_snapshot(args.snapshot_date)
    print(destination.relative_to(PROJECT_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
