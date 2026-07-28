"""创建 WA Trader v2 首轮详细日报并追加同日 cycle 更新。

作用：汇总版本身份、订单、券商结果、资金、持仓、风险和后续事项。
重要性：日报是人类可读的当日备份；首轮只创建一次，后续运行只能追加而不能覆盖历史。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from v2.runtime import atomic_write_text


NEW_YORK_TZ = ZoneInfo("America/New_York")


def _count(document: Mapping[str, Any], key: str) -> Any:
    summary = document.get("summary", {})
    return summary.get(key, 0) if isinstance(summary, Mapping) else 0


def _detailed_report(
    *,
    state: Any,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    invocation = getattr(
        state,
        "invocation",
        None,
    )
    is_live = bool(
        getattr(
            invocation,
            "live",
            str(
                getattr(
                    state,
                    "profile_id",
                    "",
                )
            ).startswith("live"),
        )
    )
    account = reconciliation.get("account", {})
    account = account if isinstance(account, Mapping) else {}
    capital = reconciliation.get("capital", {})
    capital = capital if isinstance(capital, Mapping) else {}
    guidance = context.get("initial_guidance", {})
    guidance = (
        guidance if isinstance(guidance, Mapping) else {}
    )
    base = context.get("base_snapshot", {})
    base = base if isinstance(base, Mapping) else {}
    coarse = context.get("coarse", {})
    coarse = coarse if isinstance(coarse, Mapping) else {}
    portfolio = context.get("portfolio", {})
    portfolio = (
        portfolio if isinstance(portfolio, Mapping) else {}
    )
    execution = context.get("execution", {})
    execution = (
        execution if isinstance(execution, Mapping) else {}
    )
    allocation = portfolio.get("allocation", {})
    allocation = (
        allocation
        if isinstance(allocation, Mapping)
        else {}
    )
    market = execution.get("market_assessment", {})
    market = market if isinstance(market, Mapping) else {}
    capital_plan = portfolio.get(
        "capital_deployment_plan",
        {},
    )
    capital_plan = (
        capital_plan
        if isinstance(capital_plan, Mapping)
        else {}
    )
    portfolio_decisions = [
        item
        for item in portfolio.get("decisions", [])
        if isinstance(item, Mapping)
    ]
    strategy_lines: list[str] = []
    for item in portfolio_decisions:
        valuation = item.get("valuation", {})
        valuation = (
            valuation
            if isinstance(valuation, Mapping)
            else {}
        )
        expected = item.get(
            "expected_return",
            {},
        )
        expected = (
            expected
            if isinstance(expected, Mapping)
            else {}
        )
        accumulation = item.get(
            "accumulation_plan",
            {},
        )
        accumulation = (
            accumulation
            if isinstance(accumulation, Mapping)
            else {}
        )
        strategy_lines.append(
            f"- {item.get('symbol')}："
            f"{item.get('action')} → "
            f"{item.get('target_weight')}；"
            f"风险桶={item.get('risk_bucket', '未提供')}；"
            f"价格={valuation.get('market_price')}，"
            f"价值区间={valuation.get('value_range_low')}"
            f"–{valuation.get('value_range_high')}，"
            f"证据={valuation.get('evidence_quality')}；"
            f"bear/base/bull="
            f"{expected.get('bear_annualized')}/"
            f"{expected.get('base_annualized')}/"
            f"{expected.get('bull_annualized')}；"
            f"建仓={accumulation.get('style')} "
            f"{accumulation.get('planned_total_fraction')}"
        )
    portfolio_strategy_lines = (
        "\n".join(strategy_lines)
        or "- 本版本没有结构化估值/分批建仓结论。"
    )
    validated_orders = [
        item
        for item in validated.get("orders", [])
        if isinstance(item, Mapping)
    ]
    order_lines = (
        "\n".join(
            (
                f"- {item.get('symbol')}："
                f"{item.get('status')} / "
                f"{item.get('side')} "
                f"{item.get('quantity')} / "
                f"{item.get('order_class', 'simple')} "
                f"{item.get('order_type')}；"
                f"limit={item.get('limit_price')}，"
                f"stop={item.get('stop_price') or item.get('stop_loss_stop_price')}，"
                f"take-profit={item.get('take_profit_limit_price')}，"
                f"trail={item.get('trail_price') or item.get('trail_percent')}，"
                f"保护角色={item.get('protection_role', 'none')}"
            )
            for item in validated_orders
        )
        or "- 无"
    )
    positions = [
        item
        for item in reconciliation.get("positions", [])
        if isinstance(item, Mapping)
    ]
    position_lines = (
        "\n".join(
            (
                f"- {item.get('symbol')}："
                f"qty={item.get('quantity')}，"
                f"value={item.get('market_value')}"
            )
            for item in positions
        )
        or "- 无持仓"
    )
    return (
        f"# WA Trader v2 日报 — {state.run_date}\n\n"
        "## 运行身份\n\n"
        f"- Profile：{state.profile_id}\n"
        f"- 环境：{'live' if is_live else 'paper'}\n"
        f"- 账户 hash：{str(account.get('account_id_hash', 'unknown'))[:12]}\n"
        f"- App：{state.release.get('app_version')}\n"
        f"- Strategy：{state.release.get('strategy_id')}@{state.release.get('strategy_version')}\n"
        f"- Risk：{state.release.get('risk_profile')}\n"
        f"- Order policy：{state.release.get('order_policy')}\n"
        f"- Submission policy：{state.release.get('submission_policy')}\n\n"
        "## Initial guidance\n\n"
        f"- 模式：{guidance.get('mode', 'unknown')}\n"
        f"- 内容：{guidance.get('raw_text') or '无'}\n\n"
        "## 市场与决策摘要\n\n"
        f"- 市场阶段：{base.get('market_phase', 'unknown')}\n"
        f"- Coarse 状态/数量：{coarse.get('status', 'unknown')} / "
        f"{coarse.get('selection_count', 0)}\n"
        f"- Coarse 摘要：{coarse.get('market_summary', '未提供')}\n"
        f"- 市场结论：{market.get('summary', '未提供')}\n"
        f"- 目标现金：{allocation.get('target_cash_weight')}\n"
        f"- 目标持仓数：{allocation.get('target_position_count')}\n"
        f"- 月度贡献规划：CNY "
        f"{capital_plan.get('expected_monthly_contribution_cny', '未提供')}"
        f"（{capital_plan.get('contribution_status', 'unknown')}）\n"
        f"- Execution 状态：{execution.get('status', 'unknown')}\n\n"
        "### 长期估值与分批建仓\n\n"
        f"{portfolio_strategy_lines}\n\n"
        "## 本轮决策与订单\n\n"
        f"- Cycle：{state.cycle_id}\n"
        f"- 类型：{state.cycle_kind.value}\n"
        f"- 拟定：{_count(validated, 'proposed')}\n"
        f"- 批准：{_count(validated, 'approved')}\n"
        f"- Dry-run 批准：{_count(validated, 'dry_run_approved')}\n"
        f"- 提交：{submission.get('submitted_count', 0)}\n"
        f"- 既有幂等订单：{submission.get('existing_count', 0)}\n"
        f"- 取消确认：{submission.get('cancel_confirmed_count', 0)}\n\n"
        "### Proposed / validated 与止盈止损明细\n\n"
        f"{order_lines}\n\n"
        "## 对账\n\n"
        f"- 成交：{_count(reconciliation, 'filled')}\n"
        f"- 部分成交：{_count(reconciliation, 'partially_filled')}\n"
        f"- Open：{_count(reconciliation, 'open')}\n"
        f"- 拒绝：{_count(reconciliation, 'rejected')}\n"
        f"- 不确定：{_count(reconciliation, 'uncertain')}\n"
        f"- Cash：{capital.get('cash')}\n"
        f"- Buying power：{capital.get('buying_power')}\n"
        f"- Portfolio value：{capital.get('portfolio_value')}\n\n"
        "### 当前持仓\n\n"
        f"{position_lines}\n\n"
        "## 风险与后续事项\n\n"
        f"- 需要下一轮再平衡：{reconciliation.get('requires_next_cycle_rebalance', False)}\n"
        f"- 原因：{', '.join(str(x) for x in reconciliation.get('reasons', [])) or '无'}\n"
        f"- 警告：{len(reconciliation.get('warnings', []))}\n"
        f"- 错误：{len(reconciliation.get('errors', []))}\n"
    )


def _incremental_update(
    *,
    state: Any,
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
) -> str:
    now = datetime.now(NEW_YORK_TZ).strftime("%H:%M ET")
    capital = reconciliation.get("capital", {})
    capital = capital if isinstance(capital, Mapping) else {}
    positions = [
        str(item.get("symbol"))
        for item in reconciliation.get("positions", [])
        if isinstance(item, Mapping)
        and item.get("symbol")
    ]
    tracked_count = len(
        reconciliation.get("tracked_orders", [])
    )
    return (
        f"\n\n## {now} 更新\n\n"
        f"- Cycle：{state.cycle_id}\n"
        f"- 类型：{state.cycle_kind.value}\n"
        f"- 旧订单变化/跟踪数：{tracked_count}\n"
        f"- 新提交：{submission.get('submitted_count', 0)}\n"
        f"- 成交：{_count(reconciliation, 'filled')}\n"
        f"- 部分成交：{_count(reconciliation, 'partially_filled')}\n"
        f"- 拒绝/取消：{_count(reconciliation, 'rejected')}/"
        f"{_count(reconciliation, 'canceled')}\n"
        f"- Buying power：{capital.get('buying_power')}\n"
        f"- 当前持仓变化：{', '.join(positions) or '无持仓'}\n"
        f"- 下一轮建议：{', '.join(str(x) for x in reconciliation.get('reasons', [])) or '常规execution refresh'}\n"
    )


def update_daily_report(
    path: Path,
    *,
    state: Any,
    validated: Mapping[str, Any],
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Return True only when this call created the detailed daily report."""

    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if f"- Cycle：{state.cycle_id}\n" in existing:
            return False
        atomic_write_text(
            path,
            existing
            + _incremental_update(
                state=state,
                submission=submission,
                reconciliation=reconciliation,
            ),
        )
        return False
    atomic_write_text(
        path,
        _detailed_report(
            state=state,
            validated=validated,
            submission=submission,
            reconciliation=reconciliation,
            context=context or {},
        ),
    )
    return True
