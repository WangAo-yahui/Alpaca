"""验证策略文件集合与运行时内容身份。

作用：确保 manifest 约束文件集合，同时让当前源码的小内容修改立即形成新 hash。
重要性：手工热运行必须可审计，又不能因未同步 manifest hash 阻止安全小改动。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from v2.releases import (
    load_strategy_release,
    sha256_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StrategyReleaseTests(unittest.TestCase):
    def test_current_content_is_materialized_into_hash(
        self,
    ) -> None:
        release = load_strategy_release(
            "core_long",
            "1.0.0",
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(len(release.release_hash), 64)
        documented = load_strategy_release(
            "core_long",
            "1.0.1",
            project_root=PROJECT_ROOT,
        )
        self.assertEqual(
            documented.strategy_version,
            "1.0.1",
        )
        self.assertEqual(
            len(documented.release_hash),
            64,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(
                PROJECT_ROOT / "strategies",
                root / "strategies",
            )
            prompt = (
                root
                / "strategies/core_long/1.0.0"
                / "prompts/coarse.md"
            )
            prompt.write_text(
                prompt.read_text(encoding="utf-8")
                + "\nmodified\n",
                encoding="utf-8",
            )
            changed = load_strategy_release(
                "core_long",
                "1.0.0",
                project_root=root,
            )
            self.assertEqual(
                changed.prompt_hashes[
                    "prompts/coarse.md"
                ],
                sha256_file(prompt),
            )
            self.assertNotEqual(
                changed.release_hash,
                release.release_hash,
            )


if __name__ == "__main__":
    unittest.main()
