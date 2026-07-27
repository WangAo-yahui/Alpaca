"""验证 Stage G 二十项最终写前门禁的关键拒绝路径。

作用：覆盖正常 paper1、live、账户 hash、产物 hash 和 emergency stop。
重要性：任何单项失败都必须在 broker 写调用之前 fail closed。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from v2.data.account import fetch_account
from v2.exceptions import SafetyBlockedError
from v2.models.orders import canonical_hash
from v2.models.state import (
    CycleKind,
    CycleStatus,
    ReviewMode,
    StepName,
    new_cycle_state,
)
from v2.profiles import (
    load_profile,
    load_submission_policy,
)
from v2.releases import sha256_file
from v2.runtime import build_cycle_paths
from v2.trading.submission_guard import (
    assert_submission_allowed,
)
from v2.trading.submission_journal import SubmissionJournal
from tests.v2.submission_support import (
    WriteTradingClient,
    clients_for,
)


class SubmissionPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = build_cycle_paths(
            cycle_id="20260724T140000",
            run_date="2026-07-24",
            project_root=self.root,
            profile_id="paper1",
            strategy_id="core_long",
            strategy_version="1.2.0",
        )
        self.profile = load_profile("paper1")
        self.policy = load_submission_policy(
            "alpaca_paper@1.0.0"
        )
        self.state = new_cycle_state(
            self.paths,
            cycle_kind=CycleKind.DAILY_FULL,
            review_mode=ReviewMode.SKIPPED_BY_FLAG,
            no_review=True,
            allow_trade=True,
            release={
                "app_version": "2.0.0",
                "git_commit": "test",
                "strategy_id": "core_long",
                "strategy_version": "1.2.0",
                "risk_profile": "paper_standard@1.1.0",
                "risk_profile_hash": "a" * 64,
                "order_policy": "paper_equity@1.0.0",
                "order_policy_hash": "b" * 64,
                "submission_policy": self.policy.reference,
                "submission_policy_hash": sha256_file(
                    self.policy.source_path
                ),
                "release_hash": "c" * 64,
                "prompt_hashes": {},
                "schema_hashes": {},
                "config_hashes": {},
            },
        )
        self.state.current_step = StepName.SUBMIT_ORDERS
        self.state.status = CycleStatus.RUNNING
        self.validated = {
            "profile_id": "paper1",
            "cycle_id": self.state.cycle_id,
            "submission_requested": True,
            "submission_performed": False,
        }
        self.specs = {
            "submission_requested": True,
            "submission_performed": False,
        }
        self.actions = {
            "submission_requested": True,
            "submission_performed": False,
        }
        self.intent = {
            "validated_orders_hash": canonical_hash(
                self.validated
            ),
            "request_specs_hash": canonical_hash(self.specs),
            "action_plan_hash": canonical_hash(self.actions),
            "submission_policy": self.policy.reference,
            "submission_policy_hash": sha256_file(
                self.policy.source_path
            ),
        }
        self.clients = clients_for(WriteTradingClient())
        self.account = fetch_account(self.clients)
        self.journal = SubmissionJournal.load_or_create(
            self.paths.submission_journal,
            profile_id="paper1",
            run_date="2026-07-24",
            cycle_id="20260724T140000",
            operations=[],
        )

    def check(self, **overrides: object) -> None:
        values = {
            "profile": self.profile,
            "policy": self.policy,
            "state": self.state,
            "clients": self.clients,
            "account": self.account,
            "expected_account_hash": self.account[
                "account_id_hash"
            ],
            "validated": self.validated,
            "request_specs": self.specs,
            "action_plan": self.actions,
            "intent": self.intent,
            "journal": self.journal,
            "broker_submission_exists": False,
        }
        values.update(overrides)
        assert_submission_allowed(**values)

    def test_all_gates_pass(self) -> None:
        self.check()

    def test_account_hash_mismatch_blocks(self) -> None:
        with self.assertRaises(SafetyBlockedError):
            self.check(expected_account_hash="0" * 64)

    def test_artifact_hash_change_blocks(self) -> None:
        changed = dict(self.validated)
        changed["manual_edit"] = True
        with self.assertRaises(SafetyBlockedError):
            self.check(validated=changed)

    def test_live_flag_blocks(self) -> None:
        self.state.invocation = SimpleNamespace(
            allow_trade=True,
            live=True,
        )
        with self.assertRaises(SafetyBlockedError):
            self.check()

    def test_emergency_stop_blocks(self) -> None:
        self.policy.settings["deployment_switches"][
            "emergency_stop"
        ] = True
        with self.assertRaises(SafetyBlockedError):
            self.check()

    def test_protective_order_switch_blocks(
        self,
    ) -> None:
        specs = {
            **self.specs,
            "requests": [
                {
                    "protection_role": "pt-stop",
                }
            ],
        }
        intent = {
            **self.intent,
            "request_specs_hash": canonical_hash(
                specs
            ),
        }
        self.policy.settings[
            "deployment_switches"
        ][
            "protective_order_submission_enabled"
        ] = False
        with self.assertRaises(SafetyBlockedError):
            self.check(
                request_specs=specs,
                intent=intent,
            )


if __name__ == "__main__":
    unittest.main()
