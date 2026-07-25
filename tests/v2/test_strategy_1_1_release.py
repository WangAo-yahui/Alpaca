"""验证 core_long 1.1.0 的完整能力与 1.0.0 coarse 内容不变。"""

from __future__ import annotations

import unittest

from v2.profiles import load_profile
from v2.releases import load_strategy_release


class StrategyOneOneReleaseTests(unittest.TestCase):
    def test_release_and_paper2_upgrade(self) -> None:
        release = load_strategy_release(
            "core_long",
            "1.1.0",
        )
        self.assertIn(
            "prompts/portfolio.md",
            release.prompt_hashes,
        )
        self.assertIn(
            "schemas/portfolio_output.schema.json",
            release.schema_hashes,
        )
        self.assertEqual(
            load_profile(
                "paper2"
            ).strategy_version,
            "1.1.0",
        )

    def test_coarse_artifacts_match_1_0_0(
        self,
    ) -> None:
        for relative in (
            "prompts/coarse.md",
            "prompts/coarse_AGENTS.md",
            "schemas/coarse_output.schema.json",
            "config/coarse_policy.json",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(
                    (
                        load_strategy_release(
                            "core_long",
                            "1.0.0",
                        ).root
                        / relative
                    ).read_bytes(),
                    (
                        load_strategy_release(
                            "core_long",
                            "1.1.0",
                        ).root
                        / relative
                    ).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
