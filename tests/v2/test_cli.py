from __future__ import annotations

import unittest

from v2.cli import parse_cli_args


class CLITests(unittest.TestCase):
    def test_defaults_to_paper(self) -> None:
        options = parse_cli_args(
            ["--profile", "default"]
        )
        self.assertTrue(options.paper)
        self.assertFalse(options.live)
        self.assertFalse(options.no_review)

    def test_no_need_review_aliases(self) -> None:
        for flag in (
            "--no-review",
            "--no-need-review",
            "--no_need_review",
        ):
            with self.subTest(flag=flag):
                self.assertTrue(
                    parse_cli_args(
                        [
                            "--profile",
                            "default",
                            flag,
                        ]
                    ).no_review
                )

    def test_live_is_parsed_for_main_to_reject(
        self,
    ) -> None:
        options = parse_cli_args(
            ["--profile", "default", "--live"]
        )
        self.assertTrue(options.live)
        self.assertFalse(options.paper)

    def test_conflicting_cycle_modes_rejected(
        self,
    ) -> None:
        with self.assertRaises(SystemExit):
            parse_cli_args(
                [
                    "--profile",
                    "default",
                    "--force-full",
                    "--force-rebalance",
                ]
            )

    def test_cycle_id_and_new_cycle_conflict(
        self,
    ) -> None:
        with self.assertRaises(SystemExit):
            parse_cli_args(
                [
                    "--profile",
                    "default",
                    "--cycle-id",
                    "20260723T120502",
                    "--new-cycle",
                ]
            )

    def test_profile_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            parse_cli_args([])


if __name__ == "__main__":
    unittest.main()
