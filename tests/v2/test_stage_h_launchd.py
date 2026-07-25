"""验证 Stage H launchd plist 和服务控制命令。

作用：检查 plist 仅调用防重入入口、不含密钥，并使用用户 gui domain。
重要性：launchd 是自动运行边界，错误参数可能绕过 current release 或泄漏凭据。
"""

from __future__ import annotations

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from v2.deployment.launchd import (
    LaunchdController,
    build_plist,
)
from v2.deployment.paths import DeploymentPaths


class FakeLaunchdRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        self.calls.append(arguments)
        output = (
            "pid = 123\n"
            if arguments[:2]
            == ["launchctl", "print"]
            else ""
        )
        return subprocess.CompletedProcess(
            arguments, 0, output, ""
        )


class StageHLaunchdTests(unittest.TestCase):
    def test_plist_has_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = DeploymentPaths.for_project(
                root, home=root / "home"
            )
            payload = build_plist(paths)
            serialized = plistlib.dumps(payload)
            self.assertIn(b"_service-run", serialized)
            self.assertNotIn(
                b"ALPACA_API_KEY", serialized
            )
            self.assertNotIn(
                b"ALPACA_SECRET_KEY", serialized
            )
            self.assertNotIn(
                b"account_id", serialized.lower()
            )

    def test_controller_uses_gui_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = DeploymentPaths.for_project(
                root, home=root / "home"
            )
            paths.logs.mkdir(
                parents=True, exist_ok=True
            )
            runner = FakeLaunchdRunner()
            controller = LaunchdController(
                paths,
                runner=runner,
                uid=501,
            )
            controller.install_and_start()
            status = controller.status()
            self.assertTrue(status["loaded"])
            self.assertTrue(status["running"])
            flattened = " ".join(
                " ".join(call)
                for call in runner.calls
            )
            self.assertIn("gui/501", flattened)


if __name__ == "__main__":
    unittest.main()
