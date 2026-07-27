"""验证顶层 wa 命令解析和 JSON/退出码合同。

作用：用替身管理器检查 status、health、run 和隐藏 service-run 的路由。
重要性：所有人工作业与 launchd 必须走同一稳定入口，不能出现不同安全语义。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from v2.deployment.cli import main
from v2.deployment.constants import ExitCode


class StageHCLITests(unittest.TestCase):
    def test_status_json_and_run_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = MagicMock()
            manager.status.return_value = {
                "profile_id": "paper1",
                "trading_enabled": False,
            }
            manager.run.return_value = ExitCode.NO_ACTION
            with patch(
                "v2.deployment.cli.DeploymentManager",
                return_value=manager,
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    status_code = main(
                        ["status", "--json"],
                        project_root=Path(temporary),
                    )
                run_code = main(
                    ["run", "--force-full"],
                    project_root=Path(temporary),
                )
            self.assertEqual(status_code, 0)
            self.assertIn('"paper1"', output.getvalue())
            self.assertEqual(
                run_code, int(ExitCode.NO_ACTION)
            )
            manager.run.assert_called_once_with(
                allow_trade=False,
                force_full=True,
            )

    def test_service_run_uses_current_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = MagicMock()
            manager.service_run.return_value = (
                ExitCode.SUCCESS
            )
            with patch(
                "v2.deployment.cli.DeploymentManager",
                return_value=manager,
            ):
                code = main(
                    ["_service-run"],
                    project_root=Path(temporary),
                )
            self.assertEqual(code, 0)
            manager.service_run.assert_called_once_with()

    def test_run_routes_maintenance_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = MagicMock()
            manager.profile_environment = "live"
            manager.run.return_value = ExitCode.SUCCESS
            with patch(
                "v2.deployment.cli.DeploymentManager",
                return_value=manager,
            ):
                code = main(
                    [
                        "run",
                        "--live",
                        "--maintenance-only",
                    ],
                    project_root=Path(temporary),
                )
            self.assertEqual(code, 0)
            manager.run.assert_called_once_with(
                allow_trade=False,
                force_full=False,
                maintenance_only=True,
            )


if __name__ == "__main__":
    unittest.main()
