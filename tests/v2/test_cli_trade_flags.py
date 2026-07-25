from __future__ import annotations

import contextlib
import io
import unittest

from v2.cli import parse_cli_args


class CLITradeFlagTests(unittest.TestCase):
    def test_all_no_review_aliases(self) -> None:
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

    def test_all_allow_trade_aliases(self) -> None:
        for flag in (
            "--allow-trade",
            "--allow_trade",
        ):
            with self.subTest(flag=flag):
                options = parse_cli_args(
                    [
                        "--profile",
                        "default",
                        flag,
                    ]
                )
                self.assertTrue(
                    options.allow_trade
                )
                self.assertTrue(options.paper)
                self.assertFalse(options.live)

    def test_default_is_dry_run_permission(
        self,
    ) -> None:
        options = parse_cli_args(
            [
                "--profile",
                "default",
                "--no-review",
            ]
        )
        self.assertFalse(options.allow_trade)

    def test_allow_trade_conflicts_with_maintenance(
        self,
    ) -> None:
        with contextlib.redirect_stderr(
            io.StringIO()
        ):
            with self.assertRaises(SystemExit):
                parse_cli_args(
                    [
                        "--profile",
                        "default",
                        "--allow-trade",
                        "--maintenance-only",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
