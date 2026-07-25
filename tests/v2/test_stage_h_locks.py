"""验证 Stage H deploy/run 锁的原子、防重入和 stale 恢复。

作用：模拟活跃持锁进程、死亡 PID 与正常释放。
重要性：锁错误会直接导致重复 paper 提交或 current/previous 指针竞态。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from v2.deployment.locks import (
    LockAlreadyHeldError,
    ProcessLock,
)


class StageHLockTests(unittest.TestCase):
    def test_live_lock_blocks_second_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper1.run.lock"
            first = ProcessLock(path, "first")
            first.acquire()
            try:
                with self.assertRaises(
                    LockAlreadyHeldError
                ):
                    ProcessLock(path, "second").acquire()
            finally:
                first.release()
            self.assertFalse(path.exists())

    def test_dead_pid_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deploy.lock"
            path.write_text(
                json.dumps(
                    {
                        "pid": 99999999,
                        "command": "dead",
                    }
                ),
                encoding="utf-8",
            )
            lock = ProcessLock(path, "replacement")
            lock.acquire()
            try:
                payload = json.loads(
                    path.read_text(encoding="utf-8")
                )
                self.assertEqual(payload["pid"], os.getpid())
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
