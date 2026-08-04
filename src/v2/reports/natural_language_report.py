"""按实质变化使用额外 Codex 调用生成并维护自然语言交易日报。

作用：把确定性日报、持仓、当日订单、组合与执行结论交给 Codex，联网补充有来源的新闻并给出未来策略指导。
重要性：账户与成交事实只能来自本地落盘数据；同日后续运行只维护真实变化，禁止为了定时调用而强行改变策略。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from v2.codex.runner import (
    _execute,
    sanitized_codex_environment,
)
from v2.exceptions import TemporaryDataError
from v2.runtime import (
    atomic_write_json,
    atomic_write_text,
    utc_now_iso,
)


NEW_YORK_TZ = ZoneInfo("America/New_York")
NO_UPDATE_MARKER = "NO_MATERIAL_UPDATE"


@dataclass(frozen=True)
class NaturalReportResult:
    path: Path
    updated: bool
    status: str


def natural_report_path(
    daily_report_path: Path,
) -> Path:
    """Return the date-scoped narrative path."""

    return (
        daily_report_path.parent
        / "natural_language"
        / f"{daily_report_path.stem}.md"
    )


def natural_report_output_path(
    daily_report_path: Path,
) -> Path:
    """Return the date-scoped raw Codex narrative output path."""

    return (
        daily_report_path.parent
        / "natural_language_report_output"
        / f"{daily_report_path.stem}.md"
    )


def natural_report_state_path(
    daily_report_path: Path,
) -> Path:
    """Return the date-scoped narrative state path."""

    return (
        daily_report_path.parent
        / ".natural_language_report"
        / "state"
        / f"{daily_report_path.stem}.json"
    )


def natural_report_error_path(
    daily_report_path: Path,
) -> Path:
    """Return the date-scoped optional-call error path."""

    return (
        daily_report_path.parent
        / ".natural_language_report"
        / "errors"
        / f"{daily_report_path.stem}.json"
    )


def legacy_natural_report_path(
    daily_report_path: Path,
) -> Path:
    """Return the stable latest-narrative path."""

    return (
        daily_report_path.parent
        / "natural_language"
        / "latest.md"
    )


def _mixed_report_path(
    daily_report_path: Path,
) -> Path:
    """Return the pre-separation date-scoped report path."""

    return daily_report_path.with_name(
        f"{daily_report_path.stem}.natural.md"
    )


def _mixed_report_state_path(
    daily_report_path: Path,
) -> Path:
    """Return the pre-separation state path."""

    return daily_report_path.with_name(
        f"{daily_report_path.stem}.natural.state.json"
    )


def _migrate_mixed_report_artifacts(
    daily_report_path: Path,
) -> None:
    """Copy older mixed-layout artifacts into the separated layout once."""

    report_path = natural_report_path(
        daily_report_path
    )
    if not report_path.is_file():
        for candidate in (
            _mixed_report_path(daily_report_path),
            daily_report_path.with_name(
                "natural_language_report.md"
            ),
        ):
            if candidate.is_file():
                atomic_write_text(
                    report_path,
                    candidate.read_text(
                        encoding="utf-8"
                    ),
                )
                break

    state_path = natural_report_state_path(
        daily_report_path
    )
    mixed_state_path = _mixed_report_state_path(
        daily_report_path
    )
    if (
        not state_path.is_file()
        and mixed_state_path.is_file()
    ):
        atomic_write_text(
            state_path,
            mixed_state_path.read_text(
                encoding="utf-8"
            ),
        )


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _account_material_hash(
    facts: Mapping[str, Any],
) -> str:
    reconciliation = facts.get("reconciliation")
    reconciliation = (
        reconciliation
        if isinstance(reconciliation, Mapping)
        else {}
    )
    positions = reconciliation.get("positions")
    positions = (
        positions
        if isinstance(positions, list)
        else []
    )
    normalized_positions = sorted(
        (
            dict(item)
            for item in positions
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(
            item.get("symbol", "")
        ),
    )
    capital = reconciliation.get("capital")
    capital = (
        dict(capital)
        if isinstance(capital, Mapping)
        else {}
    )
    material_order_fields = (
        "client_order_id",
        "symbol",
        "side",
        "type",
        "time_in_force",
        "quantity",
        "notional",
        "filled_quantity",
        "average_fill_price",
        "limit_price",
        "stop_price",
        "order_class",
        "trail_price",
        "trail_percent",
        "high_water_mark",
        "legs",
        "status",
        "extended_hours",
    )
    orders_by_id: dict[str, dict[str, Any]] = {}
    for collection_name in (
        "today_orders",
        "tracked_orders",
        "open_orders",
    ):
        collection = reconciliation.get(
            collection_name
        )
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, Mapping):
                continue
            client_order_id = str(
                item.get("client_order_id", "")
            )
            if not client_order_id:
                continue
            orders_by_id[client_order_id] = {
                field: item.get(field)
                for field in material_order_fields
            }
    normalized_orders = sorted(
        orders_by_id.values(),
        key=lambda item: str(
            item.get("client_order_id", "")
        ),
    )
    validated = facts.get(
        "validated_orders"
    )
    validated = (
        validated
        if isinstance(validated, Mapping)
        else {}
    )
    validated_fields = (
        "symbol",
        "status",
        "side",
        "order_type",
        "quantity",
        "limit_price",
        "order_class",
        "stop_price",
        "trail_price",
        "trail_percent",
        "take_profit_limit_price",
        "stop_loss_stop_price",
        "stop_loss_limit_price",
        "protection_role",
        "reason_codes",
    )
    normalized_validated = sorted(
        (
            {
                field: item.get(field)
                for field in validated_fields
            }
            for item in validated.get(
                "orders",
                [],
            )
            if isinstance(item, Mapping)
        ),
        key=lambda item: (
            str(item.get("symbol", "")),
            str(
                item.get(
                    "protection_role",
                    "",
                )
            ),
            str(item.get("side", "")),
        ),
    )
    context = facts.get("context")
    context = (
        context
        if isinstance(context, Mapping)
        else {}
    )
    execution = context.get("execution")
    execution = (
        execution
        if isinstance(execution, Mapping)
        else {}
    )
    normalized_protection_plans = sorted(
        (
            dict(item)
            for item in execution.get(
                "protection_plans",
                [],
            )
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(
            item.get("symbol", "")
        ),
    )
    portfolio = context.get("portfolio")
    portfolio = (
        portfolio if isinstance(portfolio, Mapping) else {}
    )
    normalized_portfolio = {
        "allocation": portfolio.get("allocation", {}),
        "cash_management": portfolio.get("cash_management", {}),
        "decisions": sorted(
            (
                dict(item)
                for item in portfolio.get("decisions", [])
                if isinstance(item, Mapping)
            ),
            key=lambda item: str(item.get("symbol", "")),
        ),
        "requires_rebalance_next_cycle": portfolio.get(
            "requires_rebalance_next_cycle"
        ),
    }
    return _canonical_hash(
        {
            "positions": normalized_positions,
            "capital": capital,
            "tracked_orders": normalized_orders,
            "validated_orders": (
                normalized_validated
            ),
            "protection_plans": (
                normalized_protection_plans
            ),
            "portfolio": normalized_portfolio,
        }
    )


def _cycle_has_material_event(
    facts: Mapping[str, Any],
) -> bool:
    submission = facts.get("broker_submission")
    submission = (
        submission
        if isinstance(submission, Mapping)
        else {}
    )
    for field in (
        "submitted_count",
        "cancel_confirmed_count",
        "uncertain_count",
        "rejected_count",
    ):
        try:
            if int(submission.get(field, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _valid_narrative(
    narrative: str,
    *,
    initial: bool,
) -> bool:
    stripped = narrative.strip()
    if not stripped:
        return False
    if stripped == NO_UPDATE_MARKER:
        return True
    lowered = stripped.lower()
    if (
        "已按 `instructions.md`" in stripped
        or ".natural_language_report/" in stripped
        or "[natural_language_report.md]" in lowered
    ):
        return False
    if initial:
        return (
            stripped.startswith(
                "# WA Trader v2 自然语言日报"
            )
            and "## 当前持仓分析" in stripped
            and "## 当日订单解读" in stripped
            and "## 未来策略指导" in stripped
        )
    required = (
        "### 发生的变化",
        "### 订单/持仓影响",
        "### 新闻更新",
        "### 策略是否调整",
        "### 下一次关注",
    )
    return (
        stripped.startswith("## ")
        and all(
            heading in stripped
            for heading in required
        )
    )


def _sync_legacy_report_alias(
    daily_report_path: Path,
    report_path: Path,
) -> None:
    if report_path.is_file():
        alias = legacy_natural_report_path(
            daily_report_path
        )
        alias.parent.mkdir(parents=True, exist_ok=True)
        temporary_alias = alias.with_name(
            f".{alias.name}.tmp"
        )
        temporary_alias.unlink(missing_ok=True)
        temporary_alias.symlink_to(report_path.name)
        temporary_alias.replace(alias)


def _bounded_text(path: Path, limit: int = 120_000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    return (
        text
        if len(text) <= limit
        else text[-limit:]
    )


def _report_prompt(*, initial: bool) -> str:
    mode = (
        "这是当天第一份自然语言日报，必须输出完整报告。"
        if initial
        else (
            "这是同日维护轮次。先比较 previous_natural_report.md、"
            "deterministic_daily_report.md 和 facts.json。"
            "没有实质性的持仓、订单、资金、市场判断或重大新闻变化时，"
            f"只输出一行 {NO_UPDATE_MARKER}。有变化时只输出需要追加的"
            "维护内容，不重复整份日报，也不得为了本次调用强行建议换仓。"
        )
    )
    return f"""你是 WA Trader v2 的实盘日报分析员。{mode}

读取：
- facts.json：本轮唯一可信的账户、持仓、订单、组合和执行事实；
- deterministic_daily_report.md：当天从程序事实生成的结构化日报；
- previous_natural_report.md：同日已有自然语言日报，可能为空。

硬要求：
1. 使用中文自然语言 Markdown，不输出 JSON。除股票代码、公司或产品专名、
   URL、文件路径、程序字段名和必须原样保留的状态码外，标题、解释、结论、
   表格列名及金融术语全部使用中文；不得输出英文句子。必须保留的英文术语
   首次出现时应紧邻给出中文含义。
2. 绝不能编造成交、价格、盈亏、持仓、账户资金、订单原因或新闻。
3. 账户、持仓、订单与资金数字只能来自 facts.json 或确定性日报。
4. 必须联网搜索与当前持仓、候选标的及大盘相关的当日新闻；只使用可核验来源，并在新闻段落给出 Markdown 链接和发布日期/事件时间。
5. 明确区分：既有持仓、今日计划、已提交订单、已成交、未成交/部分成交/拒绝、未来建议。
6. 分析前面日报和当前持仓，包括集中度、现金、未实现盈亏、订单对风险暴露的影响。
7. 解读当日每一笔订单；没有订单时说明为什么保持不变可能是合理结果。
8. 给出未来策略指导，包括继续持有、观察、减仓、加仓或等待的触发条件；建议不是已执行事实。
9. 允许明确结论“维持当前策略，无需调整”。不得把定时运行理解为每次必须交易。
10. 不披露或猜测 account id、API key、secret。
11. 直接把完整报告正文或维护正文作为最终回答返回；不得创建或修改文件，不得只返回文件路径、链接或“已完成”说明。
12. 本轮只要存在新提交、成交、部分成交、拒绝、取消、挂单状态变化，或持仓/现金发生变化，就属于实质变化，绝不能输出 {NO_UPDATE_MARKER}。
13. 若前序日报中的账户快照已被本轮事实取代，必须明确说明旧快照已过时，并以本轮 facts.json 为准。
14. 必须单独核对止盈止损：区分 Codex 保护计划、Python 降级后的 validated 保护单、已提交/已挂券商保护单；写明 order class、覆盖数量、触发价、限价、止盈价或 trailing 参数，并指出尚未被实际保护的持仓，不能把计划中的保护误写成已生效。
15. 若 portfolio 提供 valuation、expected_return 和 accumulation_plan，逐个说明市场价格与价值区间的关系、证据质量、bear/base/bull 回报假设及当前应执行的 tranche；不得把估值模型写成事实。
16. 不得写入每月固定入金金额或频率；用户只会不定期投入不确定金额。只有 facts.json 已显示可用 USD 后才属于可部署资金；若检测到 USDT，先说明其兑换 USD 及券商确认状态。
17. 不得强制防守、满仓或分散；100% 现金、接近满仓、平衡、集中以及可论证的中等回撤都可以是模型结果，但必须写清机会成本、永久损失风险和改变结论的条件。
18. `protection mode=none` 必须表述为“没有生效的券商自动保护”，并列出替代的 thesis/估值/集中度复查条件。
19. 若 portfolio 为 `success_local_only` 或多个标的共享同一个上游证据缺口，先用一段话说明决策时点的共同原因，不要把同一句“证据不足”机械复制到每个标的。只补充各标的特有缺口。自然语言日报本次联网取得的新资料属于报告时点背景，不能倒写成 portfolio 决策时已经掌握的证据；应明确说明需在下一次完整组合研究中重新评估。
20. 净入金、净入金后盈亏、每日和累计时间加权收益只能原样使用 `facts.json` 的 `context.performance`；不得自行从券商原始盈亏重算。必须明确区分券商原始组合盈亏与外部现金流校正后的时间加权收益。`status=partial` 或 `unavailable` 时说明近似或缺失，不能补造数字。

第一份完整报告结构：
# WA Trader v2 自然语言日报 — 日期
## 今日结论
## 前序日报与账户变化
## 当前持仓分析
## 当日订单解读
## 市场与持仓相关新闻
## 风险与资金使用
## 未来策略指导
## 下次计划维护关注项

同日维护有变化时结构：
## HH:MM ET 自然语言维护
### 发生的变化
### 订单/持仓影响
### 新闻更新
### 策略是否调整
### 下一次关注
"""


def write_fallback_natural_language_report(
    daily_report_path: Path,
    *,
    state: Any,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
) -> NaturalReportResult:
    """Write a factual narrative when Codex/news connectivity is unavailable."""

    _migrate_mixed_report_artifacts(
        daily_report_path
    )
    report_path = natural_report_path(
        daily_report_path
    )
    state_path = natural_report_state_path(
        daily_report_path
    )
    capital = reconciliation.get("capital", {})
    capital = (
        capital
        if isinstance(capital, Mapping)
        else {}
    )
    positions = [
        item
        for item in reconciliation.get(
            "positions", []
        )
        if isinstance(item, Mapping)
    ]
    orders = [
        item
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
    ]
    portfolio = context.get("portfolio", {})
    portfolio = (
        portfolio
        if isinstance(portfolio, Mapping)
        else {}
    )
    allocation = portfolio.get("allocation", {})
    allocation = (
        allocation
        if isinstance(allocation, Mapping)
        else {}
    )
    execution = context.get("execution", {})
    execution = (
        execution
        if isinstance(execution, Mapping)
        else {}
    )
    performance = context.get("performance", {})
    performance = (
        performance
        if isinstance(performance, Mapping)
        else {}
    )
    market = execution.get(
        "market_assessment", {}
    )
    market = (
        market
        if isinstance(market, Mapping)
        else {}
    )
    execution_decisions = {
        str(item.get("symbol", "")).upper(): item
        for item in execution.get("decisions", [])
        if isinstance(item, Mapping)
        and item.get("symbol")
    }
    portfolio_decisions = [
        item
        for item in portfolio.get("decisions", [])
        if isinstance(item, Mapping)
    ]
    portfolio_lines = (
        "\n".join(
            (
                f"- {item.get('symbol')}："
                f"组合动作 {item.get('action')}，"
                f"目标权重 {item.get('target_weight')}，"
                f"确信度 {item.get('conviction')}。"
            )
            for item in portfolio_decisions
        )
        or "- 本轮没有组合目标。"
    )
    position_lines = (
        "\n".join(
            (
                f"- {item.get('symbol')}："
                f"数量 {item.get('quantity')}，"
                f"市值 {item.get('market_value')}，"
                f"未实现盈亏 {item.get('unrealized_pl', 'unknown')}。"
            )
            for item in positions
        )
        or "- 当前没有可读取持仓。"
    )
    order_lines = (
        "\n".join(
            (
                f"- {item.get('symbol')}："
                f"状态 {item.get('status')}，"
                f"方向 {item.get('side')}，"
                f"数量 {item.get('quantity')}，"
                f"类型 {item.get('order_class', 'simple')}/{item.get('order_type')}，"
                f"止盈 {item.get('take_profit_limit_price')}，"
                f"止损 {item.get('stop_price') or item.get('stop_loss_stop_price')}，"
                f"止损限价 {item.get('stop_loss_limit_price') or (item.get('limit_price') if item.get('order_type') == 'stop_limit' else None)}，"
                f"移动参数 {item.get('trail_price') or item.get('trail_percent')}，"
                f"保护角色 {item.get('protection_role', 'none')}，"
                f"原因 {', '.join(str(value) for value in item.get('reason_codes', [])) or '无'}。"
                + (
                    " 执行判断："
                    + str(
                        execution_decisions.get(
                            str(
                                item.get(
                                    "symbol",
                                    "",
                                )
                            ).upper(),
                            {},
                        ).get(
                            "decision_reason",
                            "未提供",
                        )
                    )
                )
            )
            for item in orders
        )
        or "- 本轮没有拟定或校验订单。"
    )
    submitted_count = int(
        submission.get("submitted_count", 0) or 0
    )
    if submitted_count > 0:
        action_guidance = (
            f"- 本轮已有 {submitted_count} 笔订单提交；"
            "后续策略必须先根据对账结果区分成交、部分成交、挂单、拒绝"
            "或不确定状态，再决定是否调整，不能把已提交等同于已成交。\n"
        )
    elif orders:
        action_guidance = (
            "- 本轮存在拟定或校验订单，但没有已确认提交；状态和阻止原因"
            "见上。下一轮应使用新鲜账户、订单与行情重新校验，不能把计划"
            "写成成交事实。\n"
        )
    else:
        action_guidance = (
            "- 本轮没有拟定或校验订单；在下一轮获得新账户、持仓、订单和"
            "行情事实前维持本轮策略。没有订单可以是合格决策，"
            "定时调用不等于每次必须换仓。\n"
        )
    initial_text = (
        f"# WA Trader v2 自然语言日报 — {state.run_date}\n\n"
        "## 今日结论\n\n"
        "- 这是断网事实版；账户和订单结论完整保留，新闻部分等待"
        " Codex 网络恢复后补充。\n"
        f"- 执行模型结论：{market.get('summary', '本轮市场判断未提供。')}\n\n"
        "## 前序日报与账户变化\n\n"
        f"- Cycle：{state.cycle_id}\n"
        f"- 账户权益：{capital.get('equity')}\n"
        f"- 现金：{capital.get('cash')}\n"
        f"- Buying power：{capital.get('buying_power')}\n"
        f"- 目标现金权重：{allocation.get('target_cash_weight')}\n"
        f"- 目标持仓数：{allocation.get('target_position_count')}\n\n"
        "### 净入金校正绩效\n\n"
        f"- 状态：{performance.get('status', 'unavailable')}\n"
        f"- 累计净入金：{performance.get('net_contributions_total')}\n"
        f"- 净入金后盈亏：{performance.get('net_profit_after_contributions')}\n"
        f"- 本日时间加权收益：{performance.get('daily_twr')}\n"
        f"- 累计时间加权收益：{performance.get('cumulative_twr')}\n"
        "- 上述收益只来自程序的外部现金流校正结果；不可用或部分可用时不补算。\n\n"
        "## 当前持仓分析\n\n"
        f"{position_lines}\n\n"
        "当前持仓事实来自 Alpaca 对账；本降级报告不推断缺失成本、"
        "成交或未落盘盈亏。\n\n"
        "### 当前组合目标\n\n"
        f"{portfolio_lines}\n\n"
        "## 当日订单解读\n\n"
        f"{order_lines}\n\n"
        f"- 已提交：{submitted_count}\n"
        f"- 不确定：{submission.get('uncertain_count', 0)}\n\n"
        "## 市场与持仓相关新闻\n\n"
        "- Codex/新闻网络当前不可用，本报告没有联网新闻，"
        "也没有用旧闻或猜测填充。后续计划运行会自动重试并仅追加"
        "可核验的新闻变化。\n\n"
        "## 风险与资金使用\n\n"
        "- Live 配置允许使用全部账户权益，但本轮仍受组合策略、"
        "资产能力、行情时效、点差、订单幂等和账户 buying power 约束。\n"
        "- 止盈止损只有在 broker submission 与 open/tracked orders 中"
        "可核验时才算已生效；validated/proposed 仅代表计划或本地批准。\n"
        f"- 当前 reconciliation 错误数："
        f"{len(reconciliation.get('errors', []))}；"
        f"警告数：{len(reconciliation.get('warnings', []))}。\n\n"
        "## 未来策略指导\n\n"
        f"{action_guidance}"
        "- 若订单成交、部分成交、拒绝，或持仓/现金发生实质变化，"
        "下一轮重新计算执行与风险暴露。\n"
        "- 新闻策略指导将在 Codex 网络恢复后追加；当前不以未核验新闻"
        "改变仓位。\n\n"
        "## 下次计划维护关注项\n\n"
        "- 持仓数量与市值；现金和 buying power；挂单状态；"
        "成交/拒绝；行情阶段；与持仓相关的当日新闻。\n"
    )
    summary = reconciliation.get("summary", {})
    summary = (
        summary
        if isinstance(summary, Mapping)
        else {}
    )
    now = datetime.now(
        NEW_YORK_TZ
    ).strftime("%H:%M ET")
    cycle_marker = (
        f"<!-- Cycle {state.cycle_id}; fallback -->"
    )
    maintenance_text = (
        f"{cycle_marker}\n\n"
        f"## {now} 事实维护（新闻降级）\n\n"
        "### 发生的变化\n\n"
        f"- Cycle：{state.cycle_id}\n"
        f"- 账户权益：{capital.get('equity')}；"
        f"现金：{capital.get('cash')}；"
        f"Buying power：{capital.get('buying_power')}。\n"
        f"- 净入金后盈亏：{performance.get('net_profit_after_contributions')}；"
        f"本日/累计时间加权收益：{performance.get('daily_twr')}/"
        f"{performance.get('cumulative_twr')}；"
        f"状态：{performance.get('status', 'unavailable')}。\n"
        f"{position_lines}\n\n"
        "### 订单/持仓影响\n\n"
        f"{order_lines}\n\n"
        f"- 本轮提交：{submitted_count}；"
        f"成交：{summary.get('filled', 0)}；"
        f"部分成交：{summary.get('partially_filled', 0)}；"
        f"Open：{summary.get('open', 0)}；"
        f"拒绝：{summary.get('rejected', 0)}。\n\n"
        "### 新闻更新\n\n"
        "- Codex/新闻网络当前不可用；本维护只记录 Alpaca 对账事实，"
        "不使用旧闻或猜测补齐。\n\n"
        "### 策略是否调整\n\n"
        f"{action_guidance}"
        "- 已成交、部分成交和挂单必须分别处理；本报告不会把提交"
        "或挂单写成成交。\n\n"
        "### 下一次关注\n\n"
        "- 新成交或拒绝、挂单状态、持仓数量、现金、buying power"
        " 与可核验的当日新闻。\n"
    )
    updated = True
    if report_path.is_file():
        previous = _bounded_text(
            report_path,
            limit=1_000_000,
        ).rstrip()
        if cycle_marker in previous:
            text = previous + "\n"
            updated = False
        else:
            text = (
                previous
                + "\n\n"
                + maintenance_text
            )
    else:
        text = initial_text
    atomic_write_text(report_path, text)
    _sync_legacy_report_alias(
        daily_report_path,
        report_path,
    )
    atomic_write_json(
        state_path,
        {
            "schema_version": "1.0",
            "profile_id": state.profile_id,
            "environment": (
                "live"
                if state.invocation.live
                else "paper"
            ),
            "run_date": state.run_date,
            "last_cycle_id": state.cycle_id,
            "last_account_material_hash": (
                _account_material_hash(
                    {
                        "reconciliation": dict(
                            reconciliation
                        )
                    }
                )
            ),
            "last_status": "fallback_without_news",
            "updated_at": utc_now_iso(),
        },
    )
    return NaturalReportResult(
        path=report_path,
        updated=updated,
        status="fallback_without_news",
    )


def update_natural_language_report(
    daily_report_path: Path,
    *,
    state: Any,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
    timeout_seconds: float = 600,
    executable: str = "codex",
    model: str | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
) -> NaturalReportResult:
    """Call Codex once and atomically create or maintain the daily narrative."""

    _migrate_mixed_report_artifacts(
        daily_report_path
    )
    report_path = natural_report_path(
        daily_report_path
    )
    state_path = natural_report_state_path(
        daily_report_path
    )
    workspace = (
        daily_report_path.parent
        / ".natural_language_report"
        / daily_report_path.stem
    )
    workspace.mkdir(parents=True, exist_ok=True)
    facts = {
        "schema_version": "1.0",
        "profile_id": state.profile_id,
        "environment": (
            "live"
            if state.invocation.live
            else "paper"
        ),
        "run_date": state.run_date,
        "cycle_id": state.cycle_id,
        "cycle_kind": state.cycle_kind.value,
        "generated_at": utc_now_iso(),
        "release": dict(state.release),
        "validated_orders": dict(validated),
        "broker_submission": dict(submission),
        "reconciliation": dict(reconciliation),
        "context": dict(context),
    }
    facts_hash = _canonical_hash(facts)
    account_material_hash = (
        _account_material_hash(facts)
    )
    existing_state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            loaded = json.loads(
                state_path.read_text(encoding="utf-8")
            )
            if isinstance(loaded, dict):
                existing_state = loaded
        except (OSError, json.JSONDecodeError):
            existing_state = {}
    if (
        existing_state.get("last_cycle_id")
        == state.cycle_id
        and existing_state.get("last_status")
        in {"updated", "no_material_update"}
    ):
        _sync_legacy_report_alias(
            daily_report_path,
            report_path,
        )
        return NaturalReportResult(
            path=report_path,
            updated=False,
            status="already_processed",
        )
    material_changed = (
        existing_state.get(
            "last_account_material_hash"
        )
        != account_material_hash
        or _cycle_has_material_event(facts)
    )

    initial = not report_path.is_file()
    if not initial and not material_changed:
        _sync_legacy_report_alias(
            daily_report_path,
            report_path,
        )
        atomic_write_json(
            state_path,
            {
                "schema_version": "1.0",
                "profile_id": state.profile_id,
                "environment": facts["environment"],
                "run_date": state.run_date,
                "last_cycle_id": state.cycle_id,
                "last_facts_hash": facts_hash,
                "last_account_material_hash": account_material_hash,
                "last_material_event": False,
                "last_status": "no_material_update",
                "updated_at": utc_now_iso(),
            },
        )
        return NaturalReportResult(
            path=report_path,
            updated=False,
            status="skipped_no_material_change",
        )
    atomic_write_json(
        workspace / "facts.json",
        facts,
    )
    atomic_write_text(
        workspace / "deterministic_daily_report.md",
        _bounded_text(daily_report_path),
    )
    atomic_write_text(
        workspace / "previous_natural_report.md",
        _bounded_text(report_path),
    )
    atomic_write_text(
        workspace / "instructions.md",
        _report_prompt(initial=initial),
    )
    output = natural_report_output_path(
        daily_report_path
    )
    candidate = workspace / "natural_language_report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    candidate.unlink(missing_ok=True)
    command = [
        executable,
        "exec",
        *(
            ["--model", model]
            if model is not None
            else []
        ),
        *(
            [
                "--config",
                (
                    "model_reasoning_effort="
                    f'"{reasoning_effort}"'
                ),
            ]
            if reasoning_effort is not None
            else []
        ),
        *(
            [
                "--config",
                f'model_verbosity="{verbosity}"',
            ]
            if verbosity is not None
            else []
        ),
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--config",
        'web_search="live"',
        "--cd",
        str(workspace),
        "--output-last-message",
        str(output),
        (
            "Read instructions.md, facts.json, "
            "deterministic_daily_report.md and "
            "previous_natural_report.md. Follow instructions.md exactly. "
            "Return the report text directly; do not create or edit files "
            "and do not return a path or completion message. "
            "This task is natural_language_report."
        ),
    ]
    codex_environment = (
        sanitized_codex_environment()
    )
    # The Codex CLI already retries its own transport. A second raw TLS
    # preflight is useful for the long decision stages but can falsely block
    # this optional fourth call during brief VPN listener churn.
    codex_environment[
        "WA_SKIP_CODEX_NETWORK_PROBE"
    ] = "1"
    codex_environment[
        "WA_ALLOW_CODEX_NETWORK_RETRIES"
    ] = "1"
    completed = _execute(
        command,
        cwd=workspace,
        env=codex_environment,
        timeout=min(float(timeout_seconds), 600.0),
    )
    if completed.returncode != 0:
        raise TemporaryDataError(
            "Codex自然语言日报生成失败",
            code="NATURAL_REPORT_CODEX_FAILED",
            details={
                "return_code": completed.returncode,
            },
        )
    output_narrative = (
        output.read_text(encoding="utf-8").strip()
        if output.is_file()
        else ""
    )
    candidate_narrative = (
        candidate.read_text(
            encoding="utf-8"
        ).strip()
        if candidate.is_file()
        else ""
    )
    if _valid_narrative(
        output_narrative,
        initial=initial,
    ):
        narrative = output_narrative
    elif _valid_narrative(
        candidate_narrative,
        initial=initial,
    ):
        narrative = candidate_narrative
    else:
        raise TemporaryDataError(
            "Codex自然语言日报没有返回有效正文",
            code="NATURAL_REPORT_CONTENT_INVALID",
            details={
                "output_present": bool(
                    output_narrative
                ),
                "workspace_candidate_present": bool(
                    candidate_narrative
                ),
            },
        )

    updated = narrative != NO_UPDATE_MARKER
    if not updated and material_changed:
        raise TemporaryDataError(
            "账户或订单事实已有变化，Codex却未生成维护内容",
            code=(
                "NATURAL_REPORT_MATERIAL_UPDATE_MISSING"
            ),
        )
    if updated:
        if initial:
            combined = narrative.rstrip() + "\n"
        else:
            now = datetime.now(
                NEW_YORK_TZ
            ).strftime("%H:%M ET")
            combined = (
                _bounded_text(report_path, limit=1_000_000).rstrip()
                + f"\n\n<!-- Cycle {state.cycle_id}; {now} -->\n\n"
                + narrative.rstrip()
                + "\n"
            )
        atomic_write_text(report_path, combined)
    _sync_legacy_report_alias(
        daily_report_path,
        report_path,
    )

    atomic_write_json(
        state_path,
        {
            "schema_version": "1.0",
            "profile_id": state.profile_id,
            "environment": facts["environment"],
            "run_date": state.run_date,
            "last_cycle_id": state.cycle_id,
            "last_facts_hash": facts_hash,
            "last_account_material_hash": (
                account_material_hash
            ),
            "last_material_event": (
                _cycle_has_material_event(facts)
            ),
            "last_status": (
                "updated"
                if updated
                else "no_material_update"
            ),
            "updated_at": utc_now_iso(),
        },
    )
    return NaturalReportResult(
        path=report_path,
        updated=updated,
        status=(
            "updated"
            if updated
            else "no_material_update"
        ),
    )
