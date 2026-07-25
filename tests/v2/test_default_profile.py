"""验证唯一真实账户 paper1 的安全默认选择。

作用：覆盖省略 --profile 和未来 profile 文件不存在的部署路径。
重要性：默认身份不能漂移到 disabled 或 live 账户。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.config import load_config
from tests.v2.support import copy_v2_config


class DefaultProfileTests(unittest.TestCase):
    def test_default_is_paper1_and_examples_are_optional(
        self,
    ) -> None:
        self.assertEqual(
            parse_cli_args([]).profile,
            "paper1",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            for name in ("paper2.json", "paper3.json"):
                (
                    root
                    / "config/v2/profiles"
                    / name
                ).unlink()
            self.assertEqual(
                load_config(
                    project_root=root
                ).system["default_profile"],
                "paper1",
            )


if __name__ == "__main__":
    unittest.main()
