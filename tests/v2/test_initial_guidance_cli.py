from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.guidance import collect_initial_guidance
from v2.runtime import (
    build_cycle_paths,
    ensure_cycle_directories,
)


class InitialGuidanceCLITests(unittest.TestCase):
    def test_guidance_and_no_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_paths = build_cycle_paths(
                run_date="2026-07-24",
                cycle_id="20260724T100000",
                project_root=root,
                profile_id="paper2",
            )
            ensure_cycle_directories(first_paths)
            guidance = collect_initial_guidance(
                parse_cli_args(
                    [
                        "--profile",
                        "paper2",
                        "--guidance",
                        "  考虑MU\r\n但避免集中  ",
                    ]
                ),
                first_paths,
            )
            self.assertEqual(guidance.mode, "cli")
            self.assertEqual(
                guidance.raw_text,
                "考虑MU\n但避免集中",
            )

            second_paths = build_cycle_paths(
                run_date="2026-07-24",
                cycle_id="20260724T100001",
                project_root=root,
                profile_id="paper2",
            )
            ensure_cycle_directories(second_paths)
            skipped = collect_initial_guidance(
                parse_cli_args(
                    [
                        "--profile",
                        "paper2",
                        "--no-guidance",
                    ]
                ),
                second_paths,
            )
            self.assertEqual(
                skipped.mode,
                "skipped_by_flag",
            )

    def test_unattended_skips_both_prompts(self) -> None:
        options = parse_cli_args(
            ["--profile", "paper2", "--unattended"]
        )
        self.assertTrue(options.no_guidance)
        self.assertTrue(options.no_review)
        self.assertTrue(options.unattended)

    def test_guidance_conflicts_with_unattended(
        self,
    ) -> None:
        with self.assertRaises(SystemExit):
            parse_cli_args(
                [
                    "--profile",
                    "paper2",
                    "--guidance",
                    "MU",
                    "--unattended",
                ]
            )


if __name__ == "__main__":
    unittest.main()
