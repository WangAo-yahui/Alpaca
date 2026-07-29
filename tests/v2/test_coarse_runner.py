"""验证 Codex runner 的脱敏、重试、网络预检、心跳与有界进程清理。

作用：覆盖 WA 前台长时间无输出和网络失败时的快速、可观察退出。
重要性：Codex 卡住不能让交易轮次无限等待或遗留孤儿子进程。
"""

from __future__ import annotations

import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from v2.codex import runner as runner_module
from v2.codex.runner import (
    CodexRunner,
    PortfolioCodexRunner,
    _exact_identity_instruction,
    _execute,
    _probe_codex_network,
)
from v2.codex.workspace import (
    PortfolioWorkspace,
    prepare_coarse_workspace,
)
from v2.config import load_config
from v2.exceptions import TemporaryDataError
from v2.runtime import build_daily_paths
from tests.v2.support import (
    prepare_stage_c_project,
)


class CoarseRunnerTests(unittest.TestCase):
    def test_exact_identity_instruction_copies_input_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            input_file = Path(temp) / "input.json"
            signature = "a" * 64
            input_file.write_text(
                json.dumps(
                    {
                        "input_signature": signature,
                        "run_date": "2026-07-26",
                        "cycle_id": "cycle-1",
                        "profile": {
                            "profile_id": "paper1"
                        },
                        "release": {
                            "strategy_id": "core_long",
                            "strategy_version": "1.2.0",
                        },
                    }
                ),
                encoding="utf-8",
            )
            instruction = (
                _exact_identity_instruction(
                    input_file
                )
            )
        self.assertIn(signature, instruction)
        self.assertIn(
            '"profile_id":"paper1"',
            instruction,
        )
        self.assertIn(
            '"strategy_version":"1.2.0"',
            instruction,
        )

    def test_default_runner_caps_wait_and_disables_outer_retry(
        self,
    ) -> None:
        runner = CodexRunner(
            timeout_seconds=900,
            retry_count=1,
        )
        self.assertEqual(
            runner.timeout_seconds,
            600,
        )
        self.assertEqual(runner.retry_count, 0)

    def test_release_model_and_xhigh_reasoning_are_explicit(
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
            runner = CodexRunner(
                timeout_seconds=1,
                executor=lambda *args, **kwargs: (
                    subprocess.CompletedProcess(
                        args[0],
                        0,
                        "",
                        "",
                    )
                ),
                model="gpt-5.6-sol",
                reasoning_effort="xhigh",
                verbosity="high",
            )
            command = runner._command(workspace)
        self.assertEqual(
            command[
                command.index("--model") + 1
            ],
            "gpt-5.6-sol",
        )
        self.assertIn(
            'model_reasoning_effort="xhigh"',
            command,
        )
        self.assertIn(
            'model_verbosity="high"',
            command,
        )

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
                self.assertEqual(
                    env.get(
                        "WA_ALLOW_CODEX_NETWORK_RETRIES"
                    ),
                    "1",
                )
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
                    "WA_ALLOW_CODEX_NETWORK_RETRIES": "1",
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

    def test_timeout_recovers_fresh_workspace_output_for_validation(
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
            stale = (
                workspace.output_directory
                / "coarse_output.json"
            )
            stale.write_text(
                json.dumps({"stale": True}),
                encoding="utf-8",
            )

            def execute(
                command: list[str],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                del command, kwargs
                stale.write_text(
                    json.dumps(
                        {
                            "fresh": True,
                            "requires_stage_validation": (
                                True
                            ),
                        }
                    ),
                    encoding="utf-8",
                )
                raise subprocess.TimeoutExpired(
                    ["codex"],
                    1,
                )

            runner = CodexRunner(
                timeout_seconds=1,
                retry_count=0,
                executor=execute,
            )
            result = runner.run(workspace)

        self.assertEqual(
            result.payload,
            {
                "fresh": True,
                "requires_stage_validation": True,
            },
        )
        self.assertEqual(
            result.call_record["status"],
            (
                "completed_from_workspace_"
                "output_after_timeout"
            ),
        )
        self.assertTrue(
            result.call_record["attempts"][0][
                "workspace_output_recovered"
            ]
        )

    def test_portfolio_runner_does_not_require_coarse_output_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            temp_directory = root / ".tmp"
            temp_directory.mkdir()
            workspace = PortfolioWorkspace(
                root=root,
                agents=root / "AGENTS.md",
                input_file=root / "input.json",
                policy_file=root / "policy.json",
                risk_file=root / "risk.json",
                prompt_file=root / "prompt.md",
                schema_file=root / "schema.json",
                temp_directory=temp_directory,
                last_message=(
                    temp_directory
                    / "last_message.json"
                ),
            )
            workspace.input_file.write_text(
                json.dumps(
                    {
                        "input_signature": "a" * 64,
                        "run_date": "2026-07-29",
                    }
                ),
                encoding="utf-8",
            )

            def execute(
                command: list[str],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                del kwargs
                output_index = (
                    command.index(
                        "--output-last-message"
                    )
                    + 1
                )
                Path(
                    command[output_index]
                ).write_text(
                    json.dumps({"ok": True}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "",
                    "",
                )

            result = PortfolioCodexRunner(
                timeout_seconds=1,
                retry_count=0,
                executor=execute,
            ).run(workspace)

        self.assertEqual(
            result.payload,
            {"ok": True},
        )

    def test_network_preflight_failure_is_fast_and_not_retried(
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
                del command, kwargs
                nonlocal calls
                calls += 1
                raise TemporaryDataError(
                    "network down",
                    code="CODEX_NETWORK_UNAVAILABLE",
                )

            runner = CodexRunner(
                timeout_seconds=600,
                retry_count=1,
                executor=execute,
            )
            with self.assertRaises(
                TemporaryDataError
            ) as context:
                runner.run(workspace)
            self.assertEqual(
                context.exception.code,
                "CODEX_NETWORK_UNAVAILABLE",
            )
            self.assertEqual(calls, 1)
            assert runner.last_call_record is not None
            self.assertEqual(
                runner.last_call_record["attempts"][0][
                    "error_code"
                ],
                "CODEX_NETWORK_UNAVAILABLE",
            )

    def test_socket_preflight_maps_dns_failure(
        self,
    ) -> None:
        with patch(
            "v2.codex.runner.socket.create_connection",
            side_effect=socket.gaierror("dns"),
        ):
            with self.assertRaises(
                TemporaryDataError
            ) as context:
                _probe_codex_network(
                    timeout=1,
                    attempts=1,
                )
        self.assertEqual(
            context.exception.code,
            "CODEX_NETWORK_UNAVAILABLE",
        )

    def test_socket_preflight_maps_tls_failure(
        self,
    ) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = (
            connection
        )
        context = MagicMock()
        context.wrap_socket.side_effect = (
            ssl.SSLError("handshake failed")
        )
        with (
            patch(
                "v2.codex.runner.socket.create_connection",
                return_value=connection,
            ),
            patch(
                "v2.codex.runner.ssl.create_default_context",
                return_value=context,
            ),
        ):
            with self.assertRaises(
                TemporaryDataError
            ) as raised:
                _probe_codex_network(
                    timeout=1,
                    attempts=1,
                )
        self.assertEqual(
            raised.exception.code,
            "CODEX_NETWORK_UNAVAILABLE",
        )

    def test_socket_preflight_recovers_from_transient_failure(
        self,
    ) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = (
            connection
        )
        context = MagicMock()
        wrapped = MagicMock()
        context.wrap_socket.return_value = wrapped
        with (
            patch(
                "v2.codex.runner.socket.create_connection",
                side_effect=[
                    socket.gaierror("temporary dns"),
                    connection,
                ],
            ) as create_connection,
            patch(
                "v2.codex.runner.ssl.create_default_context",
                return_value=context,
            ),
            patch(
                "v2.codex.runner.time.sleep"
            ) as sleep,
        ):
            _probe_codex_network(
                timeout=1,
                attempts=2,
            )
        self.assertEqual(
            create_connection.call_count,
            2,
        )
        sleep.assert_called_once_with(
            runner_module
            .CODEX_CONNECTIVITY_RETRY_DELAY_SECONDS
        )

    def test_default_executor_emits_heartbeat(
        self,
    ) -> None:
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "v2.codex.runner._probe_codex_network"
            ),
            patch.object(
                runner_module,
                "CODEX_HEARTBEAT_SECONDS",
                0.01,
            ),
            redirect_stdout(output),
        ):
            completed = _execute(
                [
                    sys.executable,
                    "-c",
                    (
                        "import time; "
                        "time.sleep(0.05)"
                    ),
                ],
                cwd=Path(temp),
                env=dict(os.environ),
                timeout=2,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("仍在运行", output.getvalue())

    def test_default_executor_timeout_kills_promptly(
        self,
    ) -> None:
        started = time.monotonic()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "v2.codex.runner._probe_codex_network"
            ),
            patch.object(
                runner_module,
                "CODEX_TERMINATE_GRACE_SECONDS",
                0.1,
            ),
        ):
            with self.assertRaises(
                subprocess.TimeoutExpired
            ):
                _execute(
                    [
                        sys.executable,
                        "-c",
                        "import time; time.sleep(10)",
                    ],
                    cwd=Path(temp),
                    env=dict(os.environ),
                    timeout=0.05,
                )
        self.assertLess(
            time.monotonic() - started,
            2,
        )

    def test_default_executor_stops_network_retry_loop(
        self,
    ) -> None:
        started = time.monotonic()
        output = io.StringIO()
        environment = dict(os.environ)
        environment.pop(
            "WA_ALLOW_CODEX_NETWORK_RETRIES",
            None,
        )
        script = (
            "import sys,time; "
            "sys.stderr.write("
            "'tls handshake eof\\n' * 3"
            "); "
            "sys.stderr.flush(); "
            "time.sleep(10)"
        )
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "v2.codex.runner._probe_codex_network"
            ),
            patch.object(
                runner_module,
                "CODEX_NETWORK_FAILURE_GRACE_SECONDS",
                0.05,
            ),
            patch.object(
                runner_module,
                "CODEX_TERMINATE_GRACE_SECONDS",
                0.1,
            ),
            redirect_stdout(output),
        ):
            with self.assertRaises(
                TemporaryDataError
            ) as raised:
                _execute(
                    [
                        sys.executable,
                        "-c",
                        script,
                    ],
                    cwd=Path(temp),
                    env=environment,
                    timeout=10,
                )
        self.assertEqual(
            raised.exception.code,
            "CODEX_NETWORK_UNAVAILABLE",
        )
        self.assertLess(
            time.monotonic() - started,
            2,
        )


if __name__ == "__main__":
    unittest.main()
