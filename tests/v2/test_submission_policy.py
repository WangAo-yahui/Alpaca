"""验证版本化 Stage G paper submission policy。

作用：确认 profile 引用、部署双开关、blind retry 与 direct replace 边界。
重要性：policy 漂移不能静默扩大券商写权限。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.profiles import (
    load_profile,
    load_submission_policy,
)
from tests.v2.support import copy_v2_config


class SubmissionPolicyTests(unittest.TestCase):
    def test_paper1_pins_submission_policy(self) -> None:
        profile = load_profile("paper1")
        self.assertEqual(
            profile.submission_policy,
            "alpaca_paper@1.0.0",
        )

    def test_policy_forbids_live_replace_and_retry(self) -> None:
        policy = load_submission_policy(
            "alpaca_paper@1.0.0"
        )
        self.assertEqual(policy.environment, "paper")
        self.assertFalse(
            policy.settings["allow_direct_replace"]
        )
        self.assertEqual(
            policy.settings["write_retry"][
                "blind_retry_count"
            ],
            0,
        )
        self.assertFalse(
            policy.settings["deployment_switches"][
                "live_submission_enabled"
            ]
        )

    def test_unsafe_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            path = (
                root
                / "config/v2/submission_policies/"
                "alpaca_paper-1.0.0.json"
            )
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    '"blind_retry_count": 0',
                    '"blind_retry_count": 1',
                ),
                encoding="utf-8",
            )
            with self.assertRaises(Exception):
                load_submission_policy(
                    "alpaca_paper@1.0.0",
                    project_root=root,
                )


if __name__ == "__main__":
    unittest.main()
