"""验证 Stage D review 的跳过、人工评论、稳定 hash 与非 TTY 拒绝。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.exceptions import ConfigurationError
from v2.review import (
    REVIEW_PROMPT,
    collect_user_review,
)
from v2.runtime import create_cycle_paths


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class PortfolioReviewTests(unittest.TestCase):
    def _paths(self, root: Path):
        return create_cycle_paths(
            "2026-07-23",
            project_root=root,
            profile_id="paper2",
            strategy_id="core_long",
            strategy_version="1.1.0",
        )

    def test_unattended_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = self._paths(Path(temp))
            options = parse_cli_args(
                [
                    "--profile",
                    "paper2",
                    "--unattended",
                ]
            )
            result = collect_user_review(
                options,
                paths,
            )
            self.assertEqual(
                result.mode,
                "skipped_by_flag",
            )
            self.assertEqual(
                len(result.review_hash),
                64,
            )

    def test_human_comment_and_non_tty_safety(
        self,
    ) -> None:
        options = parse_cli_args(
            [
                "--profile",
                "paper2",
                "--guidance",
                "test",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            paths = self._paths(Path(temp))
            prompts: list[str] = []
            result = collect_user_review(
                options,
                paths,
                input_func=lambda prompt: (
                    prompts.append(prompt)
                    or "MU最多5%"
                ),
                stdin=_TTY(),
            )
            self.assertEqual(
                prompts,
                [REVIEW_PROMPT],
            )
            self.assertEqual(
                result.raw_comment,
                "MU最多5%",
            )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(
                ConfigurationError
            ):
                collect_user_review(
                    options,
                    self._paths(Path(temp)),
                    stdin=io.StringIO(),
                )


if __name__ == "__main__":
    unittest.main()
