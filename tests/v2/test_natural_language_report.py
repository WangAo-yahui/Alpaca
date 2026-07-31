"""验证自然语言日报首次创建、同日维护和幂等。

作用：使用离线假 Codex 输出验证完整日报及 NO_MATERIAL_UPDATE 语义。
重要性：每小时调用不能被解释为每小时必须改策略或重复追加日报。
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from v2.exceptions import TemporaryDataError
from v2.reports.natural_language_report import (
    _report_prompt,
    legacy_natural_report_path,
    natural_report_error_path,
    natural_report_path,
    natural_report_state_path,
    update_natural_language_report,
    write_fallback_natural_language_report,
)


INITIAL_NARRATIVE = """# WA Trader v2 自然语言日报 — 2026-07-27

## 今日结论

维持策略。

## 前序日报与账户变化

无。

## 当前持仓分析

无持仓。

## 当日订单解读

无订单。

## 市场与持仓相关新闻

无变化。

## 风险与资金使用

保持现金。

## 未来策略指导

等待新事实。

## 下次每小时维护关注项

持仓和订单。
"""


MAINTENANCE_NARRATIVE = """## 11:00 ET 自然语言维护

### 发生的变化

账户事实已更新。

### 订单/持仓影响

订单状态已更新。

### 新闻更新

无新的可核验新闻。

### 策略是否调整

维持策略。

### 下一次关注

关注订单状态。
"""


def _state(cycle_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        profile_id="live1",
        run_date="2026-07-27",
        cycle_id=cycle_id,
        cycle_kind=SimpleNamespace(
            value="execution_refresh"
        ),
        invocation=SimpleNamespace(live=True),
        release={
            "strategy_id": "core_long",
            "strategy_version": "1.2.0",
        },
    )


class NaturalLanguageReportTests(unittest.TestCase):
    def test_report_and_json_state_use_separate_directories(
        self,
    ) -> None:
        daily = Path(
            "/tmp/reports/daily/2026-07-27.md"
        )
        report = natural_report_path(daily)
        latest = legacy_natural_report_path(daily)
        state = natural_report_state_path(daily)
        error = natural_report_error_path(daily)

        self.assertEqual(
            report,
            Path(
                "/tmp/reports/daily/natural_language/2026-07-27.md"
            ),
        )
        self.assertEqual(
            latest.parent,
            report.parent,
        )
        self.assertEqual(latest.suffix, ".md")
        self.assertEqual(
            state.parent.name,
            "state",
        )
        self.assertEqual(
            error.parent.name,
            "errors",
        )
        self.assertNotEqual(
            report.parent,
            state.parent,
        )

    def test_mixed_layout_is_migrated_without_rewriting_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = (
                Path(temporary)
                / "daily"
                / "2026-07-27.md"
            )
            daily.parent.mkdir(parents=True)
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            mixed_report = daily.with_name(
                "2026-07-27.natural.md"
            )
            mixed_report.write_text(
                INITIAL_NARRATIVE,
                encoding="utf-8",
            )

            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    MAINTENANCE_NARRATIVE
                ),
            ):
                result = update_natural_language_report(
                    daily,
                    state=_state("20260727T100000"),
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )

            self.assertTrue(result.updated)
            self.assertTrue(
                result.path.read_text(
                    encoding="utf-8"
                ).startswith(
                    INITIAL_NARRATIVE.rstrip()
                )
            )
            self.assertEqual(
                mixed_report.read_text(
                    encoding="utf-8"
                ),
                INITIAL_NARRATIVE,
            )

    def test_prompt_requires_chinese_prose(
        self,
    ) -> None:
        prompt = _report_prompt(initial=True)
        self.assertIn(
            "不得输出英文句子",
            prompt,
        )
        self.assertIn(
            "金融术语全部使用中文",
            prompt,
        )

    def _fake_execute(
        self,
        narrative: str,
    ):
        def execute(command, **kwargs):
            del kwargs
            output = Path(
                command[
                    command.index(
                        "--output-last-message"
                    )
                    + 1
                ]
            )
            output.write_text(
                narrative,
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                "",
                "",
            )

        return execute

    def test_create_then_skip_unchanged_maintenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    INITIAL_NARRATIVE
                ),
            ):
                first = update_natural_language_report(
                    daily,
                    state=_state("20260727T100000"),
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            self.assertTrue(first.updated)
            self.assertTrue(first.path.is_file())
            self.assertEqual(
                legacy_natural_report_path(
                    daily
                ).read_text(encoding="utf-8"),
                first.path.read_text(encoding="utf-8"),
            )

            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    "NO_MATERIAL_UPDATE"
                ),
            ):
                second = update_natural_language_report(
                    daily,
                    state=_state("20260727T110000"),
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            self.assertFalse(second.updated)
            text = first.path.read_text(
                encoding="utf-8"
            )
            self.assertNotIn(
                "NO_MATERIAL_UPDATE",
                text,
            )

    def test_same_cycle_is_not_called_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            state = _state("20260727T100000")
            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    INITIAL_NARRATIVE
                ),
            ):
                update_natural_language_report(
                    daily,
                    state=state,
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            with patch(
                "v2.reports.natural_language_report._execute"
            ) as execute:
                result = update_natural_language_report(
                    daily,
                    state=state,
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            self.assertEqual(
                result.status,
                "already_processed",
            )
            execute.assert_not_called()

    def test_fallback_is_written_and_same_cycle_can_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            state = _state("20260727T100000")
            fallback = (
                write_fallback_natural_language_report(
                    daily,
                    state=state,
                    validated={"orders": []},
                    submission={},
                    reconciliation={
                        "capital": {
                            "equity": 100,
                            "cash": 100,
                        },
                        "positions": [],
                    },
                    context={},
                )
            )
            self.assertEqual(
                fallback.status,
                "fallback_without_news",
            )
            self.assertIn(
                "没有联网新闻",
                fallback.path.read_text(
                    encoding="utf-8"
                ),
            )
            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    MAINTENANCE_NARRATIVE
                ),
            ):
                recovered = update_natural_language_report(
                    daily,
                    state=state,
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            self.assertTrue(recovered.updated)
            self.assertIn(
                "账户事实已更新",
                recovered.path.read_text(
                    encoding="utf-8"
                ),
            )

    def test_fallback_does_not_call_submitted_order_a_fill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            result = write_fallback_natural_language_report(
                daily,
                state=_state("20260727T120000"),
                validated={
                    "orders": [
                        {
                            "symbol": "DIA",
                            "status": "approved",
                            "side": "buy",
                            "quantity": "1",
                            "reason_codes": [],
                        }
                    ]
                },
                submission={
                    "submitted_count": 1,
                    "uncertain_count": 0,
                },
                reconciliation={
                    "capital": {},
                    "positions": [],
                },
                context={},
            )
            text = result.path.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "不能把已提交等同于已成交",
                text,
            )
            self.assertNotIn(
                "所有执行项均为 defer/skipped",
                text,
            )

    def test_workspace_report_recovers_meta_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )

            def execute(command, **kwargs):
                del kwargs
                output = Path(
                    command[
                        command.index(
                            "--output-last-message"
                        )
                        + 1
                    ]
                )
                output.write_text(
                    "已按 `instructions.md` 完成："
                    "[natural_language_report.md]"
                    "(.natural_language_report/report.md)",
                    encoding="utf-8",
                )
                (
                    output.parent
                    / "natural_language_report.md"
                ).write_text(
                    INITIAL_NARRATIVE,
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "",
                    "",
                )

            with patch(
                "v2.reports.natural_language_report._execute",
                execute,
            ):
                result = update_natural_language_report(
                    daily,
                    state=_state("20260727T130000"),
                    validated={},
                    submission={},
                    reconciliation={},
                    context={},
                )
            text = result.path.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "## 当前持仓分析",
                text,
            )
            self.assertNotIn(
                "已按 `instructions.md`",
                text,
            )

    def test_no_update_is_rejected_after_fill_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    INITIAL_NARRATIVE
                ),
            ):
                update_natural_language_report(
                    daily,
                    state=_state("20260727T130000"),
                    validated={},
                    submission={},
                    reconciliation={
                        "capital": {"cash": 100},
                        "positions": [],
                    },
                    context={},
                )
            with patch(
                "v2.reports.natural_language_report._execute",
                self._fake_execute(
                    "NO_MATERIAL_UPDATE"
                ),
            ):
                with self.assertRaises(
                    TemporaryDataError
                ) as raised:
                    update_natural_language_report(
                        daily,
                        state=_state(
                            "20260727T140000"
                        ),
                        validated={},
                        submission={},
                        reconciliation={
                            "capital": {
                                "cash": 50
                            },
                            "positions": [
                                {
                                    "symbol": "DIA",
                                    "quantity": 1,
                                }
                            ],
                        },
                        context={},
                    )
            self.assertEqual(
                raised.exception.code,
                (
                    "NATURAL_REPORT_"
                    "MATERIAL_UPDATE_MISSING"
                ),
            )

    def test_later_fallback_appends_instead_of_overwriting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            daily = Path(temporary) / "daily_report.md"
            daily.write_text(
                "# deterministic\n",
                encoding="utf-8",
            )
            first = write_fallback_natural_language_report(
                daily,
                state=_state("20260727T150000"),
                validated={"orders": []},
                submission={},
                reconciliation={
                    "capital": {"cash": 100},
                    "positions": [],
                },
                context={},
            )
            original = first.path.read_text(
                encoding="utf-8"
            )
            write_fallback_natural_language_report(
                daily,
                state=_state("20260727T160000"),
                validated={"orders": []},
                submission={},
                reconciliation={
                    "capital": {"cash": 50},
                    "positions": [
                        {
                            "symbol": "DIA",
                            "quantity": 1,
                            "market_value": 50,
                        }
                    ],
                    "summary": {"filled": 1},
                },
                context={},
            )
            updated = first.path.read_text(
                encoding="utf-8"
            )
            self.assertTrue(
                updated.startswith(original.rstrip())
            )
            self.assertIn(
                "## ",
                updated,
            )
            self.assertIn(
                "事实维护（新闻降级）",
                updated,
            )
            self.assertIn(
                "DIA：数量 1",
                updated,
            )


if __name__ == "__main__":
    unittest.main()
