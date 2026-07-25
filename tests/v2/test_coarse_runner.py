from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2.codex.runner import CodexRunner
from v2.codex.workspace import (
    prepare_coarse_workspace,
)
from v2.config import load_config
from v2.exceptions import TemporaryDataError
from v2.runtime import build_daily_paths
from tests.v2.support import (
    prepare_stage_c_project,
)


class CoarseRunnerTests(unittest.TestCase):
    def test_retry_and_secret_free_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            config = load_config(
                project_root=root
            )
            workspace = prepare_coarse_workspace(
                build_daily_paths(
                    "2026-07-23",
                    project_root=root,
                ),
                config=config,
                input_payload={"ok": True},
            )
            calls: list[list[str]] = []

            def execute(
                command: list[str],
                *,
                cwd: Path,
                env: dict[str, str],
                timeout: float,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                self.assertNotIn(
                    "APCA_API_KEY_ID",
                    env,
                )
                self.assertNotIn(
                    "ALPACA_SECRET_KEY",
                    env,
                )
                if len(calls) == 1:
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "",
                        "token=secret temporary failure",
                    )
                output_index = (
                    command.index(
                        "--output-last-message"
                    )
                    + 1
                )
                (cwd / command[output_index]).write_text(
                    json.dumps({"ok": True}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "",
                    "",
                )

            with patch.dict(
                os.environ,
                {
                    "APCA_API_KEY_ID": "secret",
                    "ALPACA_SECRET_KEY": "secret",
                },
            ):
                runner = CodexRunner(
                    timeout_seconds=1,
                    retry_count=1,
                    executor=execute,
                )
                result = runner.run(workspace)
            self.assertEqual(
                result.payload,
                {"ok": True},
            )
            self.assertEqual(len(calls), 2)
            command = calls[-1]
            self.assertNotIn(
                "--ask-for-approval",
                command,
            )
            self.assertIn(
                'approval_policy="never"',
                command,
            )
            self.assertNotIn("secret", str(command))
            self.assertNotIn(
                "secret",
                str(result.call_record),
            )
            self.assertEqual(
                result.call_record["attempts"][0][
                    "return_code"
                ],
                1,
            )

    def test_two_failures_stop_retrying(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            workspace = prepare_coarse_workspace(
                build_daily_paths(
                    "2026-07-23",
                    project_root=root,
                ),
                config=load_config(
                    project_root=root
                ),
                input_payload={"ok": True},
            )
            calls = 0

            def execute(
                command: list[str],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                nonlocal calls
                calls += 1
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "still failing",
                )

            runner = CodexRunner(
                timeout_seconds=1,
                retry_count=1,
                executor=execute,
            )
            with self.assertRaises(
                TemporaryDataError
            ):
                runner.run(workspace)
            self.assertEqual(calls, 2)
            assert runner.last_call_record is not None
            self.assertEqual(
                runner.last_call_record["status"],
                "failed",
            )


if __name__ == "__main__":
    unittest.main()
