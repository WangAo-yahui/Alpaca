"""验证 Stage E 安装规则和 BUILD_ORDERS 安全停点。

作用：合法输出完成 execution stage，非法输出不得安装 output。
重要性：本阶段无论是否允许交易都不能跨入订单构建。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from v2.exceptions import (
    CodexOutputValidationError,
)
from v2.models.state import (
    StageStatus,
    StepName,
)
from v2.runtime import load_json_object
from tests.v2.support import (
    FakeExecutionRunner,
    stage_e_fixture,
)


class ExecutionStageTests(unittest.TestCase):
    def test_valid_stage_stops_at_build_orders(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = stage_e_fixture(Path(temp))
            self.assertEqual(
                result.stopped_at,
                StepName.BUILD_ORDERS,
            )
            self.assertEqual(
                result.resolution.state.stages[
                    "execution"
                ].status,
                StageStatus.COMPLETED,
            )
            self.assertFalse(
                result.resolution.paths
                .proposed_orders.exists()
            )
            self.assertFalse(
                result.resolution.paths
                .broker_submission.exists()
            )

    def test_execution_timing_is_application_owned(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def long_model_window(output):
                output["generated_at"] = (
                    "2026-08-04T04:00:00+00:00"
                )
                output["valid_until"] = (
                    "2026-08-18T04:00:00+00:00"
                )

            result = stage_e_fixture(
                root,
                execution_runner=FakeExecutionRunner(
                    mutate=long_model_window
                ),
            )
            paths = result.resolution.paths
            output = load_json_object(
                paths.execution_output
            )
            generated = datetime.fromisoformat(
                output["generated_at"]
            )
            valid_until = datetime.fromisoformat(
                output["valid_until"]
            )
            self.assertEqual(
                valid_until - generated,
                timedelta(minutes=30),
            )
            call = load_json_object(
                paths.execution_codex_call
            )
            normalization = call[
                "safe_normalizations"
            ][-1]
            self.assertEqual(
                normalization["type"],
                "application_owned_execution_timing",
            )
            self.assertEqual(
                normalization["original"]["valid_until"],
                "2026-08-18T04:00:00+00:00",
            )

    def test_invalid_output_is_not_installed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def mutate(output):
                output["decisions"][0][
                    "quantity"
                ] = "1"

            with self.assertRaises(
                CodexOutputValidationError
            ):
                stage_e_fixture(
                    root,
                    execution_runner=(
                        FakeExecutionRunner(
                            mutate=mutate
                        )
                    ),
                )
            outputs = list(
                root.rglob(
                    "execution/output.json"
                )
            )
            self.assertFalse(outputs)


if __name__ == "__main__":
    unittest.main()
