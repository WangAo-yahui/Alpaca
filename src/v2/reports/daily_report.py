"""创建 WA Trader v2 首轮详细日报并追加同日 cycle 更新。

作用：汇总版本身份、订单、券商结果、资金、持仓、风险和后续事项。
重要性：日报是人类可读的当日备份；首轮只创建一次，后续运行只能追加而不能覆盖历史。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from v2.runtime import atomic_write_text


NEW_YORK_TZ = ZoneInfo("America/New_York")
CHINESE_CHARACTER = re.compile(r"[\u3400-\u9fff]")
LATEST_SUMMARY_START = "<!-- WA_LATEST_SUMMARY_START -->"
LATEST_SUMMARY_END = "<!-- WA_LATEST_SUMMARY_END -->"

ZH_VALUES = {
    "live": "实盘",
    "paper": "模拟盘",
    "unknown": "未知",
    "success": "成功",
    "success_local_only": "本地成功（联网资料不可用）",
    "complete": "完整",
    "partial": "部分可用",
    "unavailable": "不可用",
    "skipped_by_flag": "按参数跳过",
    "before_market_open": "开盘前",
    "regular_session": "常规交易时段",
    "after_market_close": "收盘后",
    "daily_full": "每日完整轮次",
    "execution_refresh": "执行刷新轮次",
    "maintenance_only": "仅维护轮次",
    "open": "新建仓",
    "increase": "加仓",
    "hold": "持有",
    "reduce": "减仓",
    "close": "清仓",
    "watch": "观察",
    "avoid": "回避",
    "quality_compounder": "优质复利",
    "broad_index": "宽基指数",
    "cyclical_value": "周期价值",
    "diversifier": "分散配置",
    "medium": "中等",
    "low": "低",
    "high": "高",
    "insufficient": "不足",
    "staged": "分批建仓",
    "no_add": "不加仓",
    "skipped": "已跳过",
    "none": "无",
    "simple": "普通单",
    "buy": "买入",
    "sell": "卖出",
    "True": "是",
    "False": "否",
    "None": "无",
    "irregular_uncommitted": "不定期且非承诺",
    "not_detected": "未检测到",
    "planned": "已规划",
    "awaiting_broker_confirmation": "等待券商确认",
    "completed": "已完成",
    "not_supported": "券商不支持",
    "non_committed": "非承诺",
}


def _zh(value: object) -> str:
    text = str(value)
    return ZH_VALUES.get(text, text)


def _chinese_summary(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "未提供"
    if CHINESE_CHARACTER.search(text):
        return text
    return "模型未返回中文摘要；详细事实保存在本轮结构化产物中。"


def _coarse_summary(coarse: Mapping[str, Any]) -> str:
    summary = _chinese_summary(
        coarse.get("market_summary")
    )
    if not summary.startswith("模型未返回"):
        return summary
    selections = coarse.get("selections", [])
    selections = (
        selections
        if isinstance(selections, list)
        else []
    )
    symbols = [
        str(item.get("symbol", "")).upper()
        for item in selections
        if isinstance(item, Mapping)
        and item.get("symbol")
    ]
    supplements = sum(
        1
        for item in selections
        if isinstance(item, Mapping)
        and item.get("selection_origin")
        == "codex_supplement"
    )
    external = coarse.get(
        "external_discoveries",
        [],
    )
    external_count = (
        len(external)
        if isinstance(external, list)
        else 0
    )
    count = coarse.get(
        "selection_count",
        len(symbols),
    )
    status = str(coarse.get("status", "unknown"))
    network_text = (
        "本轮未取得联网补充，后续计划轮次会重新尝试"
        if status == "success_local_only"
        else (
            f"其中 Codex 联网补充 {supplements} 只，"
            f"另记录 {external_count} 只外部研究线索"
        )
    )
    symbols_text = (
        "、".join(symbols)
        if symbols
        else "详见结构化候选清单"
    )
    return (
        f"本轮选出 {count} 只研究候选：{symbols_text}；"
        f"{network_text}。候选清单不等于买入结论。"
    )


def _market_summary(
    market: Mapping[str, Any],
    allocation: Mapping[str, Any],
) -> str:
    summary = _chinese_summary(
        market.get("summary")
    )
    if not summary.startswith("模型未返回"):
        return summary
    target_cash = allocation.get(
        "target_cash_weight"
    )
    try:
        target_text = (
            f"{float(str(target_cash)) * 100:.2f}%"
        )
    except (TypeError, ValueError):
        target_text = "未提供"
    return (
        "组合模型未返回中文市场段落；其结构化决策给出的"
        f"目标现金为 {target_text}。该比例来自本轮资本竞争、"
        "估值证据和集中度比较，不是固定风控下限；逐项依据见下文。"
    )


def _count(document: Mapping[str, Any], key: str) -> Any:
    summary = document.get("summary", {})
    return summary.get(key, 0) if isinstance(summary, Mapping) else 0


def _percent(value: object) -> str:
    if value is None:
        return "不可用"
    try:
        return f"{float(str(value)) * 100:.4f}%"
    except (TypeError, ValueError):
        return "不可用"


def _latest_summary(
    *,
    state: Any,
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """Render the replaceable current-day facts above preserved history."""

    capital = reconciliation.get("capital", {})
    capital = capital if isinstance(capital, Mapping) else {}
    performance = context.get("performance", {})
    performance = (
        performance if isinstance(performance, Mapping) else {}
    )
    return (
        f"{LATEST_SUMMARY_START}\n"
        "## 最新状态（以此为准）\n\n"
        f"- 轮次类型：{_zh(state.cycle_kind.value)}\n"
        f"- 提交/不确定：{submission.get('submitted_count', 0)}/"
        f"{submission.get('uncertain_count', 0)}\n"
        f"- 当前权益/现金：{performance.get('current_equity')}/"
        f"{capital.get('cash')}\n"
        f"- 累计净入金：{performance.get('net_contributions_total')}\n"
        f"- 净入金后盈亏：{performance.get('net_profit_after_contributions')}\n"
        f"- 本日时间加权收益：{_percent(performance.get('daily_twr'))}\n"
        f"- 累计时间加权收益：{_percent(performance.get('cumulative_twr'))}\n"
        f"- 绩效状态/警告/错误：{_zh(performance.get('status', 'unavailable'))}/"
        f"{len(performance.get('warnings', []))}/"
        f"{len(performance.get('errors', []))}\n"
        "- 说明：下方保留同日各轮次历史；如历史值与本节不同，以本节最新对账事实为准。\n"
        f"{LATEST_SUMMARY_END}"
    )


def _upsert_latest_summary(
    text: str,
    summary: str,
) -> str:
    start = text.find(LATEST_SUMMARY_START)
    end = text.find(LATEST_SUMMARY_END)
    if start >= 0 and end >= start:
        end += len(LATEST_SUMMARY_END)
        return text[:start] + summary + text[end:]
    first_break = text.find("\n")
    if first_break < 0:
        return text.rstrip() + "\n\n" + summary + "\n"
    return (
        text[:first_break].rstrip()
        + "\n\n"
        + summary
        + "\n"
        + text[first_break + 1 :].lstrip("\n")
    )


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
    performance = context.get("performance", {})
    performance = (
        performance if isinstance(performance, Mapping) else {}
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
            f"{_zh(item.get('action'))} → "
            f"{item.get('target_weight')}；"
            f"风险类别={_zh(item.get('risk_bucket', '未提供'))}；"
            f"价格={valuation.get('market_price')}，"
            f"价值区间={valuation.get('value_range_low')}"
            f"–{valuation.get('value_range_high')}，"
            f"证据质量={_zh(valuation.get('evidence_quality'))}；"
            f"悲观/基准/乐观年化回报="
            f"{expected.get('bear_annualized')}/"
            f"{expected.get('base_annualized')}/"
            f"{expected.get('bull_annualized')}；"
            f"建仓方式={_zh(accumulation.get('style'))} "
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
                f"{_zh(item.get('status'))} / "
                f"{_zh(item.get('side'))} "
                f"{item.get('quantity')} / "
                f"{_zh(item.get('order_class', 'simple'))} "
                f"{_zh(item.get('order_type'))}；"
                f"限价={_zh(item.get('limit_price'))}，"
                f"止损价={_zh(item.get('stop_price') or item.get('stop_loss_stop_price'))}，"
                f"止盈价={_zh(item.get('take_profit_limit_price'))}，"
                f"跟踪参数={_zh(item.get('trail_price') or item.get('trail_percent'))}，"
                f"保护角色={_zh(item.get('protection_role', 'none'))}"
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
                f"数量={item.get('quantity')}，"
                f"市值={item.get('market_value')}"
            )
            for item in positions
        )
        or "- 无持仓"
    )
    return (
        f"# WA Trader v2 日报 — {state.run_date}\n\n"
        "## 运行身份\n\n"
        f"- 运行配置：{state.profile_id}\n"
        f"- 环境：{'实盘' if is_live else '模拟盘'}\n"
        f"- 账户哈希：{str(account.get('account_id_hash', '未知'))[:12]}\n"
        f"- 程序版本：{state.release.get('app_version')}\n"
        f"- 策略：{state.release.get('strategy_id')}@{state.release.get('strategy_version')}\n"
        f"- 风险配置：{state.release.get('risk_profile')}\n"
        f"- 订单策略：{state.release.get('order_policy')}\n"
        f"- 提交策略：{state.release.get('submission_policy')}\n\n"
        "## 初始指导\n\n"
        f"- 模式：{_zh(guidance.get('mode', 'unknown'))}\n"
        f"- 内容：{guidance.get('raw_text') or '无'}\n\n"
        "## 市场与决策摘要\n\n"
        f"- 市场阶段：{_zh(base.get('market_phase', 'unknown'))}\n"
        f"- 粗选状态/数量：{_zh(coarse.get('status', 'unknown'))} / "
        f"{coarse.get('selection_count', 0)}\n"
        f"- 粗选摘要：{_coarse_summary(coarse)}\n"
        f"- 市场结论：{_market_summary(market, allocation)}\n"
        f"- 目标现金：{allocation.get('target_cash_weight')}\n"
        f"- 目标持仓数：{allocation.get('target_position_count')}\n"
        f"- 未来入金：{_zh(capital_plan.get('contribution_pattern', 'irregular_uncommitted'))}；"
        "金额和时间均不预设\n"
        f"- USDT 资金准备：{_zh(capital_plan.get('usdt_conversion_status', 'not_detected'))}；"
        "检测到后优先兑换 USD，券商确认前不计入股票购买力\n"
        f"- 执行状态：{_zh(execution.get('status', 'unknown'))}\n\n"
        "### 长期估值与分批建仓\n\n"
        f"{portfolio_strategy_lines}\n\n"
        "## 本轮决策与订单\n\n"
        f"- 轮次：{state.cycle_id}\n"
        f"- 类型：{_zh(state.cycle_kind.value)}\n"
        f"- 拟定：{_count(validated, 'proposed')}\n"
        f"- 批准：{_count(validated, 'approved')}\n"
        f"- 模拟运行批准：{_count(validated, 'dry_run_approved')}\n"
        f"- 提交：{submission.get('submitted_count', 0)}\n"
        f"- 既有幂等订单：{submission.get('existing_count', 0)}\n"
        f"- 取消确认：{submission.get('cancel_confirmed_count', 0)}\n\n"
        "### 拟定与验证后的止盈止损明细\n\n"
        f"{order_lines}\n\n"
        "## 对账\n\n"
        f"- 成交：{_count(reconciliation, 'filled')}\n"
        f"- 部分成交：{_count(reconciliation, 'partially_filled')}\n"
        f"- 未完成：{_count(reconciliation, 'open')}\n"
        f"- 拒绝：{_count(reconciliation, 'rejected')}\n"
        f"- 不确定：{_count(reconciliation, 'uncertain')}\n"
        f"- 现金：{capital.get('cash')}\n"
        f"- 购买力：{capital.get('buying_power')}\n"
        f"- 组合价值：{capital.get('portfolio_value')}\n\n"
        "## 账户绩效（净入金校正）\n\n"
        f"- 计算状态：{_zh(performance.get('status', 'unavailable'))}\n"
        f"- 当前权益：{performance.get('current_equity')}\n"
        f"- 累计净入金：{performance.get('net_contributions_total')}\n"
        f"- 净入金后盈亏：{performance.get('net_profit_after_contributions')}\n"
        f"- 本日时间加权收益：{_percent(performance.get('daily_twr'))}\n"
        f"- 累计时间加权收益：{_percent(performance.get('cumulative_twr'))}\n"
        "- 方法：按日链接收益，并在每个已识别外部现金流发生日按日初现金流校正；"
        "若现金流发生在盘中则标记为近似，不使用券商原始收益替代。\n"
        f"- 计算警告/错误：{len(performance.get('warnings', []))}/"
        f"{len(performance.get('errors', []))}\n\n"
        "### 当前持仓\n\n"
        f"{position_lines}\n\n"
        "## 风险与后续事项\n\n"
        f"- 需要下一轮再平衡：{_zh(reconciliation.get('requires_next_cycle_rebalance', False))}\n"
        f"- 原因：{', '.join(str(x) for x in reconciliation.get('reasons', [])) or '无'}\n"
        f"- 警告：{len(reconciliation.get('warnings', []))}\n"
        f"- 错误：{len(reconciliation.get('errors', []))}\n"
    )


def _incremental_update(
    *,
    state: Any,
    submission: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    context: Mapping[str, Any],
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
    performance = context.get("performance", {})
    performance = (
        performance if isinstance(performance, Mapping) else {}
    )
    return (
        f"\n\n## {now} 更新\n\n"
        f"- 轮次：{state.cycle_id}\n"
        f"- 类型：{_zh(state.cycle_kind.value)}\n"
        f"- 旧订单变化/跟踪数：{tracked_count}\n"
        f"- 新提交：{submission.get('submitted_count', 0)}\n"
        f"- 成交：{_count(reconciliation, 'filled')}\n"
        f"- 部分成交：{_count(reconciliation, 'partially_filled')}\n"
        f"- 拒绝/取消：{_count(reconciliation, 'rejected')}/"
        f"{_count(reconciliation, 'canceled')}\n"
        f"- 购买力：{capital.get('buying_power')}\n"
        f"- 本日/累计时间加权收益："
        f"{_percent(performance.get('daily_twr'))}/"
        f"{_percent(performance.get('cumulative_twr'))}\n"
        f"- 累计净入金/净入金后盈亏："
        f"{performance.get('net_contributions_total')}/"
        f"{performance.get('net_profit_after_contributions')}\n"
        f"- 当前持仓变化：{', '.join(positions) or '无持仓'}\n"
        f"- 下一轮建议：{', '.join(str(x) for x in reconciliation.get('reasons', [])) or '常规执行刷新'}\n"
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
        current = _upsert_latest_summary(
            existing,
            _latest_summary(
                state=state,
                submission=submission,
                reconciliation=reconciliation,
                context=context or {},
            ),
        )
        cycle_markers = (
            f"- Cycle：{state.cycle_id}\n",
            f"- 轮次：{state.cycle_id}\n",
        )
        if any(
            marker in existing
            for marker in cycle_markers
        ):
            if current != existing:
                atomic_write_text(path, current)
            return False
        atomic_write_text(
            path,
            current
            + _incremental_update(
                state=state,
                submission=submission,
                reconciliation=reconciliation,
                context=context or {},
            ),
        )
        return False
    detailed = _detailed_report(
        state=state,
        validated=validated,
        submission=submission,
        reconciliation=reconciliation,
        context=context or {},
    )
    atomic_write_text(
        path,
        _upsert_latest_summary(
            detailed,
            _latest_summary(
                state=state,
                submission=submission,
                reconciliation=reconciliation,
                context=context or {},
            ),
        ),
    )
    return True
