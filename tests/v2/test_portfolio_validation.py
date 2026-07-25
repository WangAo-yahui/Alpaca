"""覆盖 Stage D 权重、范围、身份、资本与禁止字段业务校验。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from v2.main import run_stage_d
from v2.models.portfolio import (
    validate_portfolio_output,
)
from v2.releases import load_strategy_release
from v2.runtime import load_json_object
from tests.v2.support import (
    FakeCoarseRunner,
    FakePortfolioRunner,
    prepare_stage_c_project,
    stage_d_clients,
    stage_d_options,
)


class PortfolioValidationTests(unittest.TestCase):
    def test_core_business_rules(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare_stage_c_project(root)
            result = run_stage_d(
                stage_d_options(),
                project_root=root,
                clients=stage_d_clients(),
                coarse_runner=FakeCoarseRunner(),
                portfolio_runner=FakePortfolioRunner(),
            )
            assert result.portfolio is not None
            baseline = result.portfolio.output
            input_payload = (
                result.portfolio.input_result.payload
            )
            release = load_strategy_release(
                "core_long",
                "1.1.0",
                project_root=root,
            )
            schema = load_json_object(
                release.root
                / "schemas"
                / "portfolio_output.schema.json"
            )
            valid = validate_portfolio_output(
                baseline,
                input_payload=input_payload,
                schema=schema,
            )
            self.assertTrue(valid.valid)
            self.assertTrue(valid.warnings)

            cases = {}
            cash = copy.deepcopy(baseline)
            cash["allocation"][
                "target_cash_weight"
            ] = "0.70"
            cases["ALLOCATION_SUM_MISMATCH"] = cash

            overweight = copy.deepcopy(baseline)
            overweight["decisions"][0][
                "target_weight"
            ] = "0.09"
            overweight["allocation"][
                "target_invested_weight"
            ] = "0.25"
            overweight["allocation"][
                "target_cash_weight"
            ] = "0.75"
            cases[
                "SINGLE_SYMBOL_LIMIT_BREACHED"
            ] = overweight

            duplicate = copy.deepcopy(baseline)
            duplicate["decisions"][1]["symbol"] = (
                duplicate["decisions"][0]["symbol"]
            )
            cases[
                "DUPLICATE_DECISION_SYMBOL"
            ] = duplicate

            forbidden = copy.deepcopy(baseline)
            forbidden["decisions"][0][
                "quantity"
            ] = "1"
            cases[
                "FORBIDDEN_OUTPUT_FIELD"
            ] = forbidden

            identity = copy.deepcopy(baseline)
            identity["profile_id"] = "paper2"
            cases["PROFILE_ID_MISMATCH"] = identity

            signature = copy.deepcopy(baseline)
            signature["input_signature"] = "0" * 64
            cases[
                "INPUT_SIGNATURE_MISMATCH"
            ] = signature

            expired = copy.deepcopy(baseline)
            expired["valid_until"] = expired[
                "generated_at"
            ]
            cases[
                "INVALID_VALIDITY_WINDOW"
            ] = expired

            outside = copy.deepcopy(baseline)
            outside["decisions"][0][
                "symbol"
            ] = "NOTINCOARSE"
            cases[
                "NEW_POSITION_NOT_ELIGIBLE"
            ] = outside

            for expected_code, payload in cases.items():
                with self.subTest(code=expected_code):
                    result_value = (
                        validate_portfolio_output(
                            payload,
                            input_payload=input_payload,
                            schema=schema,
                        )
                    )
                    codes = {
                        item["code"]
                        for item
                        in result_value.errors
                    }
                    self.assertIn(
                        expected_code,
                        codes,
                    )


if __name__ == "__main__":
    unittest.main()
