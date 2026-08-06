"""验证 core_long 1.6.2 的强制联网尝试与本地降级合同。"""

from __future__ import annotations

import unittest

from v2.profiles import load_profile
from v2.releases import load_strategy_release
from v2.runtime import load_json_object


class StrategyOneSixTwoReleaseTests(unittest.TestCase):
    def test_live1_uses_mandatory_web_attempt_release(self) -> None:
        release = load_strategy_release(
            "core_long",
            "1.6.2",
        )
        live = load_profile("live1")
        paper = load_profile("paper1")
        self.assertEqual(
            live.strategy_version,
            release.strategy_version,
        )
        self.assertEqual(
            paper.strategy_version,
            "1.2.0",
        )
        policy = load_json_object(
            release.root
            / "config"
            / "coarse_policy.json"
        )
        self.assertTrue(
            policy[
                "refresh_local_only_next_cycle"
            ]
        )
        self.assertTrue(
            policy["require_web_research_attempt"]
        )
        coarse_prompt = (
            release.root
            / "prompts"
            / "coarse.md"
        ).read_text(encoding="utf-8")
        portfolio_prompt = (
            release.root
            / "prompts"
            / "portfolio.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Write `market_summary` in Chinese",
            coarse_prompt,
        )
        self.assertIn(
            "MUST attempt live web research",
            coarse_prompt,
        )
        self.assertIn(
            "previous_portfolio",
            portfolio_prompt,
        )
        execution_schema = load_json_object(
            release.root
            / "schemas"
            / "execution_output.schema.json"
        )
        self.assertEqual(
            execution_schema["properties"]
            ["strategy_version"]["const"],
            "1.6.2",
        )


if __name__ == "__main__":
    unittest.main()
