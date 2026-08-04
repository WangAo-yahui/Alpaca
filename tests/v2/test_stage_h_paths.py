"""验证 Stage H 共享根目录和部署路径覆盖。

作用：检查 runtime、reports、market data、locks 与 plist 的隔离位置。
重要性：release 切换不得把账户状态重新写回不可变代码目录。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2.data.alpaca_client import create_alpaca_clients
from v2.deployment.paths import DeploymentPaths
from v2.exceptions import ConfigurationError
from v2.profiles import account_binding_path
from v2.releases import get_git_commit
from v2.runtime import (
    build_daily_paths,
    build_shared_data_paths,
)


class StageHPathTests(unittest.TestCase):
    def test_deployment_roots_are_separated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            paths = DeploymentPaths.for_project(
                root, home=home
            )
            resolved = root.resolve()
            self.assertEqual(
                paths.runtime,
                resolved / "var/shared/runtime",
            )
            self.assertEqual(
                paths.releases,
                resolved
                / "var/deployment/live1/releases",
            )
            self.assertEqual(
                paths.plist,
                home.resolve()
                / "Library/LaunchAgents/"
                "com.wa.trader.live1.plist",
            )
            self.assertEqual(
                paths.scheduler_state,
                resolved
                / "var/shared/runtime/scheduler/"
                "live1.json",
            )

    def test_application_environment_overrides_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = DeploymentPaths.for_project(root)
            environment = paths.application_environment(
                git_commit="a" * 40
            )
            with patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                daily = build_daily_paths(
                    "2026-07-25",
                    project_root=root,
                    profile_id="paper1",
                )
                shared = build_shared_data_paths(
                    project_root=root
                )
                binding = account_binding_path(
                    "paper1",
                    project_root=root,
                )
                commit, verified = get_git_commit(root)
            self.assertEqual(
                daily.runtime_root, paths.runtime
            )
            self.assertEqual(
                daily.daily_report,
                paths.reports
                / "accounts/paper1/strategies/"
                "core_long/1.0.0/daily/2026-07-25.md",
            )
            self.assertEqual(shared.root, paths.market_data)
            self.assertEqual(
                binding,
                paths.runtime
                / "account_bindings/paper1.json",
            )
            self.assertEqual(
                environment["PYTHONDONTWRITEBYTECODE"],
                "1",
            )
            self.assertEqual(
                environment["WA_SOURCE_TREE_HASH"],
                "unknown",
            )
            self.assertEqual(
                environment["WA_SOURCE_TREE_DIRTY"],
                "false",
            )
            self.assertEqual(commit, "a" * 40)
            self.assertTrue(verified)

    def test_relative_override_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                os.environ,
                {"WA_RUNTIME_ROOT": "relative/runtime"},
                clear=True,
            ):
                with self.assertRaises(ValueError):
                    build_daily_paths(
                        "2026-07-25",
                        project_root=Path(temporary),
                    )
            with patch.dict(
                os.environ,
                {"WA_RUNTIME_ROOT": "relative/runtime"},
                clear=True,
            ):
                with self.assertRaises(
                    ConfigurationError
                ):
                    account_binding_path(
                        "paper1",
                        project_root=Path(temporary),
                    )
            with patch.dict(
                os.environ,
                {"WA_DOTENV_PATH": "relative/.env"},
                clear=True,
            ):
                with self.assertRaises(
                    ConfigurationError
                ):
                    create_alpaca_clients(
                        project_root=Path(temporary)
                    )


if __name__ == "__main__":
    unittest.main()
