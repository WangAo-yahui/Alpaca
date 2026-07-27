"""验证 Coarse 阶段运行、复用、失败恢复和安全中断持久化。

作用：覆盖 Stage C 从基础快照到结构化候选输出的状态机行为。
重要性：Codex 失败或用户中断后必须保留旧输出并留下可恢复轮次。
"""

from __future__ import annotations

import hashlib
import signal
import tempfile
import unittest
from pathlib import Path

from v2.cli import parse_cli_args
from v2.data.alpaca_client import AlpacaClients
from v2.exceptions import (
    CodexOutputValidationError,
    TemporaryDataError,
)
from v2.main import (
    _raise_runtime_interrupted,
    run_stage_c,
)
from v2.models.state import (
    CoarseStatus,
    CycleStatus,
    StepName,
    load_cycle_state,
    load_daily_state,
)
from tests.v2.fakes import (
    FakeStockDataClient,
    FakeTradingClient,
    fake_account,
    fake_order,
    fake_position,
)
from tests.v2.support import (
    FakeCoarseRunner,
    prepare_stage_c_project,
)


def clients(
    *,
    cash: str = "10000",
) -> AlpacaClients:
    return AlpacaClients(
        trading=FakeTradingClient(
            account=fake_account(cash=cash),
            positions=[
                fake_position("S064")
            ],
            open_orders=[
                fake_order("S063")
            ],
            today_orders=[],
        ),
        stock_data=FakeStockDataClient(),
    )


def options(*extra: str):
    return parse_cli_args(
        [
            "--profile",
            "paper1",
            "--run-date",
            "2026-07-23",
            "--no-review",
            "--no-guidance",
            "--allow-trade",
            *extra,
        ]
    )


class CoarseStageTests(unittest.TestCase):
    def test_signal_interruption_persists_retryable_cycle(
        self,
    ) -> None:
        class InterruptingRunner:
            def run(self, workspace: object) -> object:
                del workspace
                _raise_runtime_interrupted(
                    signal.SIGTERM,
                    object(),
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            with self.assertRaises(
                TemporaryDataError
            ) as context:
                run_stage_c(
                    options(),
                    project_root=root,
                    clients=clients(),
                    coarse_runner=InterruptingRunner(),
                )
            self.assertEqual(
                context.exception.code,
                "RUN_INTERRUPTED",
            )
            cycle_state_path = next(
                root.glob(
                    "decision_runtime_v2/accounts/"
                    "paper1/strategies/core_long/1.2.0/"
                    "2026-07-23/cycles/*/cycle_state.json"
                )
            )
            state = load_cycle_state(
                cycle_state_path
            )
            self.assertEqual(
                state.status,
                CycleStatus.FAILED_RETRIABLE,
            )
            self.assertEqual(
                state.current_step,
                StepName.RUN_COARSE,
            )
            self.assertEqual(
                state.errors[-1].code,
                "RUN_INTERRUPTED",
            )

    def test_main_chain_stops_at_portfolio(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            runner = FakeCoarseRunner()
            result = run_stage_c(
                options(),
                project_root=root,
                clients=clients(),
                coarse_runner=runner,
            )
            self.assertEqual(runner.calls, 1)
            self.assertEqual(
                result.stopped_at,
                StepName.RUN_PORTFOLIO,
            )
            self.assertIsNotNone(result.coarse)
            assert result.coarse is not None
            self.assertEqual(
                len(
                    result.coarse.output[
                        "selections"
                    ]
                ),
                60,
            )
            self.assertTrue(
                result.resolution.paths
                .coarse_output.is_file()
            )
            self.assertFalse(
                result.resolution.paths
                .broker_submission.exists()
            )
            self.assertFalse(
                result.resolution.paths
                .portfolio_output.exists()
            )

    def test_same_day_new_cycle_reuses_despite_cash_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            runner = FakeCoarseRunner()
            first = run_stage_c(
                options(),
                project_root=root,
                clients=clients(cash="10000"),
                coarse_runner=runner,
            )
            second = run_stage_c(
                options("--new-cycle"),
                project_root=root,
                clients=clients(cash="900000"),
                coarse_runner=runner,
            )
            self.assertEqual(runner.calls, 1)
            self.assertIsNotNone(second.coarse)
            assert second.coarse is not None
            self.assertTrue(second.coarse.reused)
            self.assertIn(
                StepName.RUN_COARSE,
                second.resolution.state.skipped_steps,
            )
            self.assertEqual(
                second.stopped_at,
                StepName.RUN_PORTFOLIO,
            )
            self.assertNotEqual(
                first.resolution.paths.cycle_id,
                second.resolution.paths.cycle_id,
            )

    def test_invalid_forced_result_keeps_old_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            first = run_stage_c(
                options(),
                project_root=root,
                clients=clients(),
                coarse_runner=FakeCoarseRunner(),
            )
            output_path = (
                first.resolution.paths.coarse_output
            )
            before = hashlib.sha256(
                output_path.read_bytes()
            ).hexdigest()

            def break_output(output: dict) -> None:
                output["selections"].pop()
                output["selection_count"] = 59

            with self.assertRaises(
                CodexOutputValidationError
            ):
                run_stage_c(
                    options(
                        "--new-cycle",
                        "--force-full",
                    ),
                    project_root=root,
                    clients=clients(),
                    coarse_runner=FakeCoarseRunner(
                        mutate=break_output
                    ),
                )
            after = hashlib.sha256(
                output_path.read_bytes()
            ).hexdigest()
            self.assertEqual(before, after)
            daily = load_daily_state(
                first.resolution.paths.daily_state
            )
            self.assertEqual(
                daily.coarse_status,
                CoarseStatus.VALID,
            )
            cycle_states = sorted(
                first.resolution.paths
                .cycles_directory.glob(
                    "*/cycle_state.json"
                )
            )
            failed = load_cycle_state(
                cycle_states[-1]
            )
            self.assertEqual(
                failed.status,
                CycleStatus.FAILED_RETRIABLE,
            )
            self.assertEqual(
                failed.current_step,
                StepName.RUN_COARSE,
            )


if __name__ == "__main__":
    unittest.main()
