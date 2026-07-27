"""验证 Stage H release 白名单、hash 和原子安装。

作用：在临时 Git 仓库构建最小 release，并检查敏感文件不复制、篡改可检测。
重要性：release 是回滚信任根，文件集合或 hash 漂移必须在启动前失败。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from v2.deployment.paths import DeploymentPaths
from v2.deployment.release import (
    ReleaseBuilder,
    source_tree_fingerprint,
)


class StageHReleaseTests(unittest.TestCase):
    def _repository(self, root: Path) -> None:
        files = {
            "wa": "#!/usr/bin/env python3\n",
            "requirements.txt": "example==1.0\n",
            "requirements.lock": "example==1.0\n",
            "src/v2/app.py": '"""app"""\n',
            "config/v2/system.json": "{}\n",
            "config/universe/sp500.json": "{}\n",
            "schemas/v2/state.json": "{}\n",
            "strategies/core_long/1.2.0/manifest.json": "{}\n",
            "prompts/v2/coarse.md": "# prompt\n",
            ".env": "ALPACA_SECRET_KEY=must-not-copy\n",
            "account_bindings/paper1.json": (
                '{"account_id_hash":"must-not-copy"}\n'
            ),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(
                parents=True, exist_ok=True
            )
            path.write_text(content, encoding="utf-8")
        (root / "wa").chmod(0o755)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "add", "-f", "."],
            cwd=root,
            check=True,
        )

    def test_release_excludes_secrets_and_detects_tamper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            paths = DeploymentPaths.for_project(
                root, home=root / "home"
            )
            paths.ensure_local_directories()
            builder = ReleaseBuilder(paths)
            staged = builder.build_staging(
                git_commit="a" * 40,
                release_id="release-a",
            )
            self.assertFalse(
                (staged.root / ".env").exists()
            )
            self.assertFalse(
                (
                    staged.root
                    / "account_bindings/paper1.json"
                ).exists()
            )
            installed = builder.install(staged)
            builder.validate(installed.root)
            target = installed.root / "src/v2/app.py"
            target.chmod(0o644)
            target.write_text(
                '"""tampered"""\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                builder.validate(installed.root)

    def test_source_fingerprint_tracks_uncommitted_code(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            initial = source_tree_fingerprint(root)
            added = root / "src/v2/new_rule.py"
            added.write_text(
                '"""new rule"""\n',
                encoding="utf-8",
            )
            with_added = source_tree_fingerprint(root)
            self.assertNotEqual(initial, with_added)
            added.write_text(
                '"""changed rule"""\n',
                encoding="utf-8",
            )
            self.assertNotEqual(
                with_added,
                source_tree_fingerprint(root),
            )


if __name__ == "__main__":
    unittest.main()
