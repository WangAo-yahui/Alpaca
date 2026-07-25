"""验证 Stage H 管理器的门禁、状态、健康和稳定退出码。

作用：覆盖 no-action/uncertain 映射、自动交易验证门禁、指针切换与 JSON 状态。
重要性：运维层不得把风险阻止当作成功，也不得在没有真实 submit 证据时启动自动写入。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from v2.deployment.constants import ExitCode
from v2.deployment.manager import (
    DeploymentError,
    DeploymentManager,
    DeploymentSafetyBlocked,
    classify_application_exit,
)
from v2.deployment.release import (
    ReleaseArtifact,
    atomic_write_json,
)


class StageHManagerTests(unittest.TestCase):
    def _healthy_project(self, root: Path) -> None:
        profile = (
            root / "config/v2/profiles/paper1.json"
        )
        policy = (
            root
            / "config/v2/submission_policies/"
            "alpaca_paper-1.0.0.json"
        )
        binding = root / "account_bindings/paper1.json"
        for path in (profile, policy, binding):
            path.parent.mkdir(
                parents=True, exist_ok=True
            )
        atomic_write_json(
            profile,
            {
                "profile_id": "paper1",
                "environment": "paper",
                "enabled": True,
                "submission_policy": (
                    "alpaca_paper@1.0.0"
                ),
            },
        )
        atomic_write_json(
            policy,
            {
                "environment": "paper",
                "allow_submit": True,
                "allow_direct_replace": False,
                "deployment_switches": {
                    "live_submission_enabled": False,
                    "emergency_stop": False,
                },
            },
        )
        atomic_write_json(
            binding,
            {
                "profile_id": "paper1",
                "account_id_hash": "a" * 64,
            },
        )
        (root / ".Alpaca/bin").mkdir(parents=True)
        (root / ".Alpaca/bin/python").write_text(
            "", encoding="utf-8"
        )
        (root / ".env").write_text(
            "ALPACA_API_KEY=key\n"
            "ALPACA_SECRET_KEY=secret\n",
            encoding="utf-8",
        )

    def test_stable_exit_code_mapping(self) -> None:
        self.assertEqual(
            classify_application_exit(
                0, {"status": "completed_no_action"}
            ),
            ExitCode.NO_ACTION,
        )
        self.assertEqual(
            classify_application_exit(
                0,
                {
                    "status": (
                        "blocked_submission_uncertain"
                    )
                },
            ),
            ExitCode.SUBMISSION_UNCERTAIN,
        )
        self.assertEqual(
            classify_application_exit(
                2,
                {"status": "failed_retriable"},
            ),
            ExitCode.RETRIABLE_ERROR,
        )

    def test_enable_trading_requires_submit_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            with self.assertRaises(
                DeploymentSafetyBlocked
            ):
                manager.deploy(enable_trading=True)

    def test_doctor_checks_required_local_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._healthy_project(root)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            with (
                patch(
                    "v2.deployment.manager.shutil.which",
                    return_value="/usr/local/bin/codex",
                ),
                patch.dict(
                    "os.environ", {}, clear=True
                ),
            ):
                report = manager.doctor()
            self.assertTrue(report["healthy"])
            (root / ".env").write_text(
                "ALPACA_API_KEY=key\n",
                encoding="utf-8",
            )
            with (
                patch(
                    "v2.deployment.manager.shutil.which",
                    return_value="/usr/local/bin/codex",
                ),
                patch.dict(
                    "os.environ", {}, clear=True
                ),
            ):
                report = manager.doctor()
            failed = {
                item["name"]
                for item in report["checks"]
                if not item["ok"]
            }
            self.assertIn("credentials", failed)

    def test_bootstrap_runs_all_validation_before_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            with (
                patch.object(
                    manager, "_ensure_venv"
                ) as ensure_venv,
                patch.object(
                    manager, "_copy_account_binding"
                ) as copy_binding,
                patch.object(
                    manager, "_seed_shared_market_data"
                ) as seed_data,
                patch.object(
                    manager,
                    "doctor",
                    return_value={"healthy": True},
                ),
                patch.object(
                    manager, "run_tests"
                ) as run_tests,
                patch.object(
                    manager, "compile_check"
                ) as compile_check,
                patch.object(
                    manager, "static_write_scan"
                ) as static_scan,
                patch.object(
                    manager,
                    "_git_commit",
                    return_value="a" * 40,
                ),
                patch.object(
                    manager,
                    "_run_application",
                    return_value=ExitCode.NO_ACTION,
                ) as dry_run,
            ):
                result = manager.bootstrap()
            self.assertEqual(
                result["dry_run_exit_code"],
                int(ExitCode.NO_ACTION),
            )
            for mocked in (
                ensure_venv,
                copy_binding,
                seed_data,
                run_tests,
                compile_check,
                static_scan,
                dry_run,
            ):
                mocked.assert_called_once()

    def test_switch_current_preserves_previous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            manager.paths.ensure_local_directories()
            first = {
                "release_id": "first",
                "release_path": "/tmp/first",
            }
            second = {
                "release_id": "second",
                "release_path": "/tmp/second",
            }
            manager._switch_current(first)
            manager._switch_current(second)
            self.assertEqual(
                json.loads(
                    manager.paths.current.read_text(
                        encoding="utf-8"
                    )
                )["release_id"],
                "second",
            )
            self.assertEqual(
                json.loads(
                    manager.paths.previous.read_text(
                        encoding="utf-8"
                    )
                )["release_id"],
                "first",
            )

    def test_health_blocks_uncertain_operation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            manager.status = lambda: {
                "uncertain_operations": 1,
                "emergency_stop": False,
                "current_release": "release",
                "release_valid": True,
                "service": {"loaded": True},
                "last_cycle_status": (
                    "blocked_submission_uncertain"
                ),
            }
            self.assertEqual(
                manager.health()["status"],
                "blocked",
            )

    def test_submit_marker_requires_completed_reconciliation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            cycle = (
                manager.paths.runtime
                / "accounts/paper1/strategies/"
                "core_long/1.2.0/2026-07-25/"
                "cycles/20260725T120000"
            )
            orders = cycle / "orders"
            orders.mkdir(parents=True)
            state_path = cycle / "cycle_state.json"
            atomic_write_json(
                state_path,
                {
                    "cycle_id": "20260725T120000",
                    "current_step": "COMPLETE",
                },
            )
            atomic_write_json(
                orders / "broker_submission.json",
                {
                    "submitted_count": 1,
                    "uncertain_count": 0,
                },
            )
            atomic_write_json(
                orders / "reconciliation.json",
                {"errors": []},
            )
            manager._record_submit_verification(
                state_path,
                {
                    "cycle_id": "20260725T120000",
                    "current_step": "COMPLETE",
                },
            )
            self.assertTrue(
                manager._trading_deploy_verified()
            )

    def test_health_failure_restores_deployment_pointer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            manager.paths.ensure_local_directories()
            old_current = {
                "release_id": "old-current",
                "release_path": "/tmp/old-current",
            }
            old_previous = {
                "release_id": "old-previous",
                "release_path": "/tmp/old-previous",
            }
            atomic_write_json(
                manager.paths.current, old_current
            )
            atomic_write_json(
                manager.paths.previous, old_previous
            )
            staging = manager.paths.staging / "new"
            staging.mkdir()
            manifest = staging / "release_manifest.json"
            atomic_write_json(manifest, {})
            artifact = ReleaseArtifact(
                release_id="new",
                git_commit="a" * 40,
                root=staging,
                manifest=manifest,
                manifest_hash="b" * 64,
            )
            manager.launchd = MagicMock()
            with (
                patch.object(
                    manager,
                    "doctor",
                    return_value={"healthy": True},
                ),
                patch.object(
                    manager,
                    "_git_clean",
                    return_value=True,
                ),
                patch.object(
                    manager,
                    "_git_commit",
                    return_value="a" * 40,
                ),
                patch.object(
                    manager.release_builder,
                    "build_staging",
                    return_value=artifact,
                ),
                patch.object(
                    manager.release_builder,
                    "install",
                    return_value=artifact,
                ),
                patch.object(manager, "run_tests"),
                patch.object(manager, "compile_check"),
                patch.object(
                    manager, "static_write_scan"
                ),
                patch.object(
                    manager,
                    "_run_application",
                    return_value=ExitCode.NO_ACTION,
                ),
                patch.object(
                    manager,
                    "health",
                    return_value={
                        "status": "unhealthy"
                    },
                ),
            ):
                with self.assertRaises(
                    DeploymentError
                ):
                    manager.deploy(
                        enable_trading=False
                    )
            self.assertEqual(
                json.loads(
                    manager.paths.current.read_text(
                        encoding="utf-8"
                    )
                )["release_id"],
                "old-current",
            )
            self.assertEqual(
                json.loads(
                    manager.paths.previous.read_text(
                        encoding="utf-8"
                    )
                )["release_id"],
                "old-previous",
            )

    def test_rollback_preserves_runtime_and_swaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DeploymentManager(
                root,
                home=root / "home",
                platform_name="Darwin",
            )
            manager.paths.ensure_local_directories()
            current = {
                "release_id": "current",
                "release_path": "/tmp/current",
            }
            previous = {
                "release_id": "previous",
                "release_path": "/tmp/previous",
            }
            atomic_write_json(
                manager.paths.current, current
            )
            atomic_write_json(
                manager.paths.previous, previous
            )
            sentinel = (
                manager.paths.runtime / "sentinel.json"
            )
            atomic_write_json(sentinel, {"kept": True})
            manager.launchd = MagicMock()
            with (
                patch.object(
                    manager,
                    "_release_from_document",
                    return_value=MagicMock(),
                ),
                patch.object(
                    manager,
                    "health",
                    return_value={"status": "healthy"},
                ),
            ):
                result = manager.rollback()
            self.assertEqual(
                result["current"]["release_id"],
                "previous",
            )
            self.assertTrue(sentinel.is_file())


if __name__ == "__main__":
    unittest.main()
