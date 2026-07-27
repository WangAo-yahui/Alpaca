"""安全运行 Stage C、Stage D 与 Stage E 的单次 Codex 调用与有限重试。

作用：在隔离工作区执行结构化输出调用，并记录脱敏后的尝试信息。
重要性：统一超时、重试和环境变量白名单，防止凭据泄露或无限重试。
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import ssl
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from v2.codex.workspace import (
    CoarseWorkspace,
    ExecutionWorkspace,
    PortfolioWorkspace,
)
from v2.exceptions import (
    CodexTimeoutError,
    TemporaryDataError,
)
from v2.runtime import utc_now_iso


Executor = Callable[
    ...,
    subprocess.CompletedProcess[str],
]

CODEX_CONNECTIVITY_HOST = "chatgpt.com"
CODEX_CONNECTIVITY_PORT = 443
CODEX_CONNECTIVITY_TIMEOUT_SECONDS = 5.0
CODEX_HEARTBEAT_SECONDS = 30.0
CODEX_TERMINATE_GRACE_SECONDS = 5.0
CODEX_MAX_TIMEOUT_SECONDS = 600.0
CODEX_NETWORK_FAILURE_GRACE_SECONDS = 30.0
CODEX_NETWORK_ERROR_MARKERS = (
    "tls handshake eof",
    "failed to lookup address information",
    "dns error",
    "failed to connect to websocket",
    "error sending request for url",
    "stream disconnected before completion",
)


def _stage_label(command: list[str]) -> str:
    joined = " ".join(command).lower()
    if "portfolio" in joined:
        return "组合"
    if "execution" in joined:
        return "执行"
    return "粗选"


def _probe_codex_network(
    timeout: float = CODEX_CONNECTIVITY_TIMEOUT_SECONDS,
) -> None:
    """Fail quickly when Codex's HTTPS endpoint cannot be reached."""

    try:
        with socket.create_connection(
            (
                CODEX_CONNECTIVITY_HOST,
                CODEX_CONNECTIVITY_PORT,
            ),
            timeout=max(1.0, timeout),
        ) as connection:
            connection.settimeout(max(1.0, timeout))
            context = ssl.create_default_context()
            with context.wrap_socket(
                connection,
                server_hostname=(
                    CODEX_CONNECTIVITY_HOST
                ),
            ):
                pass
    except OSError as error:
        raise TemporaryDataError(
            "Codex网络预检失败；请检查DNS、VPN或网络连接后重试",
            code="CODEX_NETWORK_UNAVAILABLE",
            details={
                "host": CODEX_CONNECTIVITY_HOST,
                "port": CODEX_CONNECTIVITY_PORT,
                "exception_type": (
                    error.__class__.__name__
                ),
            },
        ) from error


def _stream_snapshot(
    stream: object,
    *,
    limit: int = 256_000,
) -> str:
    """Read recent subprocess output without moving its write offset."""

    try:
        descriptor = stream.fileno()  # type: ignore[attr-defined]
        size = os.fstat(descriptor).st_size
        offset = max(0, size - limit)
        payload = os.pread(
            descriptor,
            min(size, limit),
            offset,
        )
    except (AttributeError, OSError):
        return ""
    return payload.decode(
        "utf-8",
        errors="replace",
    )


def _repeated_network_failure(
    stderr: str,
    *,
    elapsed: float,
) -> bool:
    """Detect a Codex transport retry loop after a short grace period."""

    if elapsed < CODEX_NETWORK_FAILURE_GRACE_SECONDS:
        return False
    normalized = stderr.lower()
    marker_count = sum(
        normalized.count(marker)
        for marker in CODEX_NETWORK_ERROR_MARKERS
    )
    return marker_count >= 3


def _terminate_process_group(
    process: subprocess.Popen[str],
) -> None:
    """Terminate Codex and every child it spawned."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(
            timeout=CODEX_TERMINATE_GRACE_SECONDS
        )
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait()


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
    _probe_codex_network()
    label = _stage_label(command)
    print(
        f"Codex{label}已启动；单次最长等待"
        f"{timeout:g}秒",
        flush=True,
    )
    started = time.monotonic()
    next_heartbeat = (
        started + CODEX_HEARTBEAT_SECONDS
    )
    with (
        tempfile.TemporaryFile(
            mode="w+", encoding="utf-8"
        ) as stdout_file,
        tempfile.TemporaryFile(
            mode="w+", encoding="utf-8"
        ) as stderr_file,
    ):
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= timeout:
                    _terminate_process_group(process)
                    stdout_file.flush()
                    stderr_file.flush()
                    stdout_file.seek(0)
                    stderr_file.seek(0)
                    raise subprocess.TimeoutExpired(
                        command,
                        timeout,
                        output=stdout_file.read(),
                        stderr=stderr_file.read(),
                    )
                stderr_snapshot = _stream_snapshot(
                    stderr_file
                )
                if _repeated_network_failure(
                    stderr_snapshot,
                    elapsed=elapsed,
                ):
                    _terminate_process_group(process)
                    raise TemporaryDataError(
                        "Codex网络连接持续失败；"
                        "请检查VPN或网络后重试",
                        code=(
                            "CODEX_NETWORK_UNAVAILABLE"
                        ),
                        details={
                            "elapsed_seconds": round(
                                elapsed,
                                3,
                            ),
                        },
                    )
                if now >= next_heartbeat:
                    print(
                        f"Codex{label}仍在运行："
                        f"{int(elapsed)}秒；"
                        "可按 Ctrl-C 安全中断",
                        flush=True,
                    )
                    next_heartbeat = (
                        now + CODEX_HEARTBEAT_SECONDS
                    )
                time.sleep(
                    min(
                        1.0,
                        max(
                            0.01,
                            timeout - elapsed,
                        ),
                        max(
                            0.01,
                            next_heartbeat - now,
                        ),
                    )
                )
        except BaseException:
            _terminate_process_group(process)
            raise
        stdout_file.flush()
        stderr_file.flush()
        stdout_file.seek(0)
        stderr_file.seek(0)
        elapsed = time.monotonic() - started
        print(
            f"Codex{label}进程结束："
            f"{int(elapsed)}秒，"
            f"退出码{process.returncode}",
            flush=True,
        )
        return subprocess.CompletedProcess(
            command,
            int(process.returncode or 0),
            stdout_file.read(),
            stderr_file.read(),
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


def _load_json_message(
    path: Path,
    *,
    label: str = "粗选",
) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as error:
        raise TemporaryDataError(
            f"Codex未生成{label}JSON输出",
            code="CODEX_OUTPUT_MISSING",
        ) from error
    except json.JSONDecodeError as error:
        raise TemporaryDataError(
            f"Codex{label}输出不是严格JSON",
            code="CODEX_OUTPUT_NOT_JSON",
            details={
                "line": error.lineno,
                "column": error.colno,
            },
        ) from error
    if not isinstance(payload, dict):
        raise TemporaryDataError(
            f"Codex{label}输出顶层必须是对象",
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

    def __post_init__(self) -> None:
        if self.executor is _execute:
            self.timeout_seconds = min(
                float(self.timeout_seconds),
                CODEX_MAX_TIMEOUT_SECONDS,
            )
            # Codex already performs transport retries internally.
            # A second application-level attempt previously doubled a
            # network outage from 15 to almost 30 minutes.
            self.retry_count = 0

    def _command(
        self,
        workspace: CoarseWorkspace,
    ) -> list[str]:
        instruction = (
            "Read prompts/coarse.md and data/input.json, "
            "then return only the required JSON object. "
            "When status is success_local_only, include an "
            "explicit non-empty network limitation warning."
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

    def _stage_name(self) -> str:
        return "coarse_selection"

    def _label(self) -> str:
        return "粗选"

    def run(
        self,
        workspace: CoarseWorkspace,
    ) -> CodexRunResult:
        attempts: list[dict[str, Any]] = []
        command = self._command(workspace)
        last_error: BaseException | None = None
        self.last_call_record = {
            "schema_version": "1.0",
            "stage": self._stage_name(),
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
                        f"Codex{self._label()}进程执行失败",
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
                        workspace.last_message,
                        label=self._label(),
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
            except subprocess.TimeoutExpired as error:
                elapsed = (
                    time.monotonic() - started
                )
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "started_at": started_at,
                        "duration_seconds": elapsed,
                        "return_code": None,
                        "stdout": _truncate(
                            _redact(
                                str(
                                    error.output
                                    or ""
                                )
                            )
                        ),
                        "stderr": _truncate(
                            _redact(
                                str(
                                    error.stderr
                                    or ""
                                )
                            )
                        ),
                        "timed_out": True,
                    }
                )
                last_error = CodexTimeoutError(
                    f"Codex{self._label()}调用超时",
                    details={
                        "attempt": attempt_number,
                        "timeout_seconds": (
                            self.timeout_seconds
                        ),
                    },
                )
            except TemporaryDataError as error:
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
                        "error_code": error.code,
                    }
                )
                last_error = error
                if (
                    error.code
                    in {
                        "CODEX_NETWORK_UNAVAILABLE",
                        "RUN_INTERRUPTED",
                    }
                ):
                    break
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
            f"Codex{self._label()}调用失败",
            code="CODEX_CALL_FAILED",
        ) from last_error


@dataclass
class PortfolioCodexRunner(CodexRunner):
    """Use the common safe runner with Stage D's prompt and identity."""

    def _command(
        self,
        workspace: PortfolioWorkspace,
    ) -> list[str]:
        instruction = (
            "Read prompts/portfolio.md and "
            "data/portfolio_input.json, then return only "
            "the required JSON object. Express every weight "
            "and fraction as a base-one decimal string such "
            "as \"1.0\", \"0.25\", or \"0\", never as a "
            "percentage string. When status is "
            "success_local_only, include an explicit "
            "non-empty network limitation warning. "
            "Do not create orders."
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

    def _stage_name(self) -> str:
        return "portfolio_decision"

    def _label(self) -> str:
        return "组合"

    def run(
        self,
        workspace: PortfolioWorkspace,
    ) -> CodexRunResult:
        return super().run(workspace)  # type: ignore[arg-type]


@dataclass
class ExecutionCodexRunner(CodexRunner):
    """Use the safe common runner for Stage E execution intent."""

    def _command(
        self,
        workspace: ExecutionWorkspace,
    ) -> list[str]:
        instruction = (
            "Read prompts/execution.md and "
            "data/execution_input.json, then return only "
            "the required JSON object. Express every weight "
            "and execution fraction as a base-one decimal "
            "string, never as a percentage string. When "
            "status is success_local_only, include explicit "
            "non-empty network limitation warnings. "
            "Do not create orders."
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

    def _stage_name(self) -> str:
        return "execution_decision"

    def _label(self) -> str:
        return "执行"

    def run(
        self,
        workspace: ExecutionWorkspace,
    ) -> CodexRunResult:
        return super().run(workspace)  # type: ignore[arg-type]
