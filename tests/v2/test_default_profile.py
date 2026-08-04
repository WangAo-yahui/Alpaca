"""验证唯一生产账户 live1 的安全默认选择。

作用：覆盖省略 --profile 和未来 profile 文件不存在的部署路径。
重要性：删除 Paper 运行项目后，默认身份不能漂移回模拟账户。
"""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from v2.cli import parse_cli_args
from v2.config import load_config
from v2.exceptions import ConfigurationError
from v2.main import bootstrap_main
from tests.v2.support import copy_v2_config


class DefaultProfileTests(unittest.TestCase):
    def test_default_is_live1_and_paper_examples_are_optional(
        self,
    ) -> None:
        self.assertEqual(
            parse_cli_args([]).profile,
            "live1",
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
                "live1",
            )

    def test_paper_profile_is_not_operational(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            system_path = root / "config/v2/system.json"
            system = json.loads(
                system_path.read_text(encoding="utf-8")
            )
            system["operational_profiles"] = ["live1"]
            system_path.write_text(
                json.dumps(system),
                encoding="utf-8",
            )
            with self.assertRaises(
                ConfigurationError
            ) as raised:
                bootstrap_main(
                    parse_cli_args(
                        [
                            "--profile",
                            "paper1",
                            "--unattended",
                        ]
                    ),
                    project_root=root,
                )
            self.assertEqual(
                raised.exception.code,
                "PROFILE_NOT_OPERATIONAL",
            )


if __name__ == "__main__":
    unittest.main()
