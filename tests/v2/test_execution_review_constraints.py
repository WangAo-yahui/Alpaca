"""验证用户禁止和无法解释的硬限制优先于交易意图。

作用：注入结构化禁止与保守 defer 标记检查 Python 校验。
重要性：无人值守模式不得猜测用户硬限制后继续交易。
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.models.execution import (
    validate_execution_output,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    stage_e_fixture,
    valid_execution_output,
)


class ExecutionReviewConstraintTests(unittest.TestCase):
    def test_prohibition_and_unresolved_comment_defer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = stage_e_fixture(root)
            assert result.execution is not None
            source = copy.deepcopy(
                result.execution
                .input_result.payload
            )
            source["execution_snapshot"][
                "market_phase"
            ] = "regular_session"
            output = valid_execution_output(source)
            symbol = output["decisions"][0][
                "symbol"
            ]
            source["review_analysis"][
                "structured_prohibitions"
            ] = [symbol]
            source["review_analysis"][
                "requires_conservative_defer"
            ] = True
            schema = load_json_object(
                load_strategy_release(
                    "core_long",
                    "1.2.0",
                    project_root=root,
                ).root
                / "schemas/execution_output.schema.json"
            )
            codes = {
                item["code"]
                for item in (
                    validate_execution_output(
                        output,
                        input_payload=source,
                        schema=schema,
                    ).errors
                )
            }
            self.assertIn(
                "USER_PROHIBITION_VIOLATED",
                codes,
            )
            self.assertIn(
                "UNRESOLVED_REVIEW_MUST_DEFER",
                codes,
            )
            self.assertIn(
                "UNRESOLVED_REVIEW_NOT_ESCALATED",
                codes,
            )


if __name__ == "__main__":
    unittest.main()
