"""验证新交易日组合决策继承最近有效策略，但不越过策略身份。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.models.state import (
    new_daily_state,
    save_daily_state,
)
from v2.runtime import (
    atomic_write_json,
    build_cycle_paths,
    build_daily_paths,
)
from v2.stages.portfolio import (
    _previous_portfolio_context,
)


class PortfolioContinuityTests(unittest.TestCase):
    def test_new_day_loads_latest_prior_day_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior_daily = build_daily_paths(
                "2026-08-04",
                project_root=root,
                profile_id="live1",
                strategy_id="core_long",
                strategy_version="1.6.0",
            )
            prior_cycle = build_cycle_paths(
                run_date="2026-08-04",
                cycle_id="20260804T140000",
                project_root=root,
                profile_id="live1",
                strategy_id="core_long",
                strategy_version="1.6.0",
            )
            prior_plan = {
                "cycle_id": prior_cycle.cycle_id,
                "run_date": prior_cycle.run_date,
                "allocation": {
                    "target_cash_weight": "0.46"
                },
            }
            atomic_write_json(
                prior_cycle.portfolio_output,
                prior_plan,
            )
            prior_state = new_daily_state(
                prior_daily
            )
            prior_state.cycle_ids.append(
                prior_cycle.cycle_id
            )
            prior_state.latest_cycle_id = (
                prior_cycle.cycle_id
            )
            prior_state.latest_valid_portfolio_cycle_id = (
                prior_cycle.cycle_id
            )
            prior_state.latest_valid_portfolio_output_path = str(
                prior_cycle.portfolio_output
            )
            save_daily_state(
                prior_daily.daily_state,
                prior_state,
            )

            current_daily = build_daily_paths(
                "2026-08-05",
                project_root=root,
                profile_id="live1",
                strategy_id="core_long",
                strategy_version="1.6.1",
            )
            current_cycle = build_cycle_paths(
                run_date="2026-08-05",
                cycle_id="20260805T100000",
                project_root=root,
                profile_id="live1",
                strategy_id="core_long",
                strategy_version="1.6.1",
            )
            current_state = new_daily_state(
                current_daily
            )
            self.assertEqual(
                _previous_portfolio_context(
                    paths=current_cycle,
                    daily_state=current_state,
                ),
                prior_plan,
            )


if __name__ == "__main__":
    unittest.main()
