"""实现 Stage H 部署锁与 paper1 防重入运行锁。

作用：用原子文件创建记录 PID、命令和时间，并安全回收已经死亡的 stale lock。
重要性：并发运行可能重复提交订单，并发部署可能破坏 current/previous 原子切换。
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LockAlreadyHeldError(RuntimeError):
    """Raised when a verified live process owns a deployment lock."""


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class ProcessLock:
    path: Path
    command: str
    stale_after_seconds: float = 6 * 60 * 60
    acquired: bool = False

    def _existing(self) -> tuple[dict[str, Any], float]:
        age = max(
            0.0,
            time.time() - self.path.stat().st_mtime,
        )
        try:
            payload = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = {}
        return (
            payload if isinstance(payload, dict) else {},
            age,
        )

    def acquire(self) -> None:
        self.path.parent.mkdir(
            parents=True, exist_ok=True
        )
        for _ in range(2):
            payload = {
                "schema_version": "1.0",
                "pid": os.getpid(),
                "command": self.command,
                "hostname": socket.gethostname(),
                "created_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                existing, age = self._existing()
                owner = int(existing.get("pid") or 0)
                stale = (
                    not _pid_is_alive(owner)
                    or age > self.stale_after_seconds
                )
                if not stale:
                    raise LockAlreadyHeldError(
                        f"锁已由PID {owner}持有：{self.path}"
                    )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(
                descriptor, "w", encoding="utf-8"
            ) as file:
                json.dump(
                    payload,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            self.acquired = True
            return
        raise LockAlreadyHeldError(
            f"无法原子获取锁：{self.path}"
        )

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload, _ = self._existing()
            if int(payload.get("pid") or 0) == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        self.release()
