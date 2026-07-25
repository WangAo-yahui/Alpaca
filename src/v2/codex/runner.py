"""Safe subprocess wrapper for one Stage C Codex call and one retry."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from v2.codex.workspace import CoarseWorkspace
from v2.exceptions import (
    CodexTimeoutError,
    TemporaryDataError,
)
from v2.runtime import utc_now_iso


Executor = Callable[
    ...,
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True)
class CodexRunResult:
    payload: dict[str, Any]
    call_record: dict[str, Any]


def _execute(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        timeout=timeout,
        text=True,
        capture_output=True,
        check=False,
    )


def sanitized_codex_environment() -> dict[str, str]:
    """Pass only process basics; never forward broker/API secrets."""

    allowed = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "SHELL",
        "CODEX_HOME",
    )
    return {
        name: os.environ[name]
        for name in allowed
        if name in os.environ
    }


def _truncate(value: str, limit: int = 20000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n<TRUNCATED>"


def _redact(value: str) -> str:
    redacted = value
    sensitive_name_parts = (
        "SECRET",
        "TOKEN",
        "API_KEY",
        "APIKEY",
        "APCA",
        "ALPACA",
        "PASSWORD",
    )
    for name, secret in os.environ.items():
        if (
            secret
            and len(secret) >= 4
            and any(
                part in name.upper()
                for part in sensitive_name_parts
            )
        ):
            redacted = redacted.replace(
                secret,
                "<REDACTED>",
            )
    return re.sub(
        (
            r"(?i)\b(api[_-]?key|secret|token|password)"
            r"\b\s*[:=]\s*[^\s,;]+"
        ),
        r"\1=<REDACTED>",
        redacted,
    )


def _load_json_message(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as error:
        raise TemporaryDataError(
            "Codex未生成粗选JSON输出",
            code="CODEX_OUTPUT_MISSING",
        ) from error
    except json.JSONDecodeError as error:
        raise TemporaryDataError(
            "Codex粗选输出不是严格JSON",
            code="CODEX_OUTPUT_NOT_JSON",
            details={
                "line": error.lineno,
                "column": error.colno,
            },
        ) from error
    if not isinstance(payload, dict):
        raise TemporaryDataError(
            "Codex粗选输出顶层必须是对象",
            code="CODEX_OUTPUT_NOT_OBJECT",
        )
    return payload


@dataclass
class CodexRunner:
    timeout_seconds: float
    retry_count: int = 1
    executable: str = "codex"
    executor: Executor = _execute
    last_call_record: dict[str, Any] | None = field(
        default=None,
        init=False,
    )

    def _command(
        self,
        workspace: CoarseWorkspace,
    ) -> list[str]:
        instruction = (
            "Read prompts/coarse.md and data/input.json, "
            "then return only the required JSON object."
        )
        return [
            self.executable,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "workspace-write",
            "--config",
            'approval_policy="never"',
            "--config",
            'web_search="live"',
            "--cd",
            str(workspace.root),
            "--output-schema",
            str(workspace.schema_file),
            "--output-last-message",
            str(workspace.last_message),
            instruction,
        ]

    def run(
        self,
        workspace: CoarseWorkspace,
    ) -> CodexRunResult:
        attempts: list[dict[str, Any]] = []
        command = self._command(workspace)
        last_error: BaseException | None = None
        self.last_call_record = {
            "schema_version": "1.0",
            "stage": "coarse_selection",
            "status": "running",
            "working_directory": str(
                workspace.root
            ),
            "command": [
                *command[:-1],
                "<prompt omitted>",
            ],
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "attempts": attempts,
        }
        for attempt_number in range(
            1,
            self.retry_count + 2,
        ):
            if workspace.last_message.exists():
                workspace.last_message.unlink()
            started_at = utc_now_iso()
            started = time.monotonic()
            try:
                completed = self.executor(
                    command,
                    cwd=workspace.root,
                    env=(
                        sanitized_codex_environment()
                    ),
                    timeout=self.timeout_seconds,
                )
                elapsed = (
                    time.monotonic() - started
                )
                attempt = {
                    "attempt": attempt_number,
                    "started_at": started_at,
                    "duration_seconds": elapsed,
                    "return_code": (
                        completed.returncode
                    ),
                    "stdout": _truncate(
                        _redact(
                            completed.stdout or ""
                        )
                    ),
                    "stderr": _truncate(
                        _redact(
                            completed.stderr or ""
                        )
                    ),
                }
                attempts.append(attempt)
                if completed.returncode != 0:
                    last_error = TemporaryDataError(
                        "Codex粗选进程执行失败",
                        code="CODEX_PROCESS_FAILED",
                        details={
                            "return_code": (
                                completed.returncode
                            ),
                            "attempt": attempt_number,
                        },
                    )
                    continue
                try:
                    payload = _load_json_message(
                        workspace.last_message
                    )
                except TemporaryDataError as error:
                    last_error = error
                    continue
                call_record = {
                    **self.last_call_record,
                    "status": "success",
                    "attempts": attempts,
                    "completed_at": utc_now_iso(),
                }
                self.last_call_record = call_record
                return CodexRunResult(
                    payload=payload,
                    call_record=call_record,
                )
            except subprocess.TimeoutExpired:
                elapsed = (
                    time.monotonic() - started
                )
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "started_at": started_at,
                        "duration_seconds": elapsed,
                        "return_code": None,
                        "stdout": "",
                        "stderr": "",
                        "timed_out": True,
                    }
                )
                last_error = CodexTimeoutError(
                    "Codex粗选调用超时",
                    details={
                        "attempt": attempt_number,
                        "timeout_seconds": (
                            self.timeout_seconds
                        ),
                    },
                )
        assert self.last_call_record is not None
        self.last_call_record = {
            **self.last_call_record,
            "status": "failed",
            "attempts": attempts,
            "completed_at": utc_now_iso(),
            "error_code": getattr(
                last_error,
                "code",
                "CODEX_CALL_FAILED",
            ),
        }
        assert last_error is not None
        if isinstance(last_error, TemporaryDataError):
            raise last_error
        if isinstance(last_error, CodexTimeoutError):
            raise last_error
        raise TemporaryDataError(
            "Codex粗选调用失败",
            code="CODEX_CALL_FAILED",
        ) from last_error
