import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_project_root
from fetch_account import save_json_atomically
from filter_candidates import (
    ASSETS_PATH,
    collect_forced_symbols,
    evaluate_asset_eligibility,
    evaluate_symbol,
    load_asset_lookup,
    load_json_file,
)


OUTPUT_FILENAME = "coarse_universe_input.json"
SCRIPT_VERSION = "2026-07-22-explicit-asset-lookup"


def deduplicate_strings(
    values: list[str],
) -> list[str]:
    """保持原顺序去除重复字符串。"""
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = str(value).strip()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def normalize_screen_status(
    item: dict[str, Any],
) -> None:
    """
    根据最终硬过滤和风险原因重新计算状态。

    forced_include只代表必须交给Codex检查，
    不会绕过硬过滤或风险隔离。
    """
    hard_reasons = item.get(
        "hard_filter_reasons",
        [],
    )

    risk_reasons = item.get(
        "risk_filter_reasons",
        [],
    )

    if hard_reasons:
        item["screen_status"] = "excluded"
    elif risk_reasons:
        item["screen_status"] = "quarantined"
    else:
        item["screen_status"] = "eligible"


def add_asset_validation(
    item: dict[str, Any],
    asset_lookup: dict[str, dict[str, Any]],
    hard_filters: dict[str, Any],
) -> None:
    """把Alpaca资产状态合并到筛选结果。"""
    symbol = str(
        item.get("symbol", "")
    ).strip().upper()

    (
        asset_summary,
        asset_reasons,
    ) = evaluate_asset_eligibility(
        symbol=symbol,
        asset_lookup=asset_lookup,
        hard_filters=hard_filters,
    )

    item["asset"] = asset_summary

    existing_reasons = [
        str(reason)
        for reason in item.get(
            "hard_filter_reasons",
            [],
        )
    ]

    item["hard_filter_reasons"] = (
        deduplicate_strings(
            existing_reasons
            + [
                str(reason)
                for reason in asset_reasons
            ]
        )
    )

    normalize_screen_status(item)


def build_compact_entry(
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    生成第一次Codex调用使用的轻量索引。

    长日线不复制进本文件，只提供工作区内路径。
    Codex可按需读取对应的300根日线。
    """
    symbol = str(
        item.get("symbol", "")
    ).strip().upper()

    return {
        "symbol": symbol,
        "screen_status": item.get(
            "screen_status"
        ),
        "forced_include": bool(
            item.get("forced_include", False)
        ),
        "priority_score_reference": (
            item.get("priority_score")
        ),
        "metrics": item.get(
            "metrics",
            {},
        ),
        "asset": item.get(
            "asset",
            {},
        ),
        "hard_filter_reasons": item.get(
            "hard_filter_reasons",
            [],
        ),
        "risk_filter_reasons": item.get(
            "risk_filter_reasons",
            [],
        ),
        "warnings": item.get(
            "warnings",
            [],
        ),
        "raw_daily_path": (
            f"data/raw_bars/daily/{symbol}.json"
        ),
    }


def evaluate_full_universe(
    decision_input: dict[str, Any],
    screener_config: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    """评估完整股票池，但不截断为60只。"""
    project_root = get_project_root()

    market = decision_input.get(
        "market",
        {},
    )

    if not isinstance(market, dict):
        raise ValueError(
            "decision_input.json中的market必须是对象"
        )

    forced_symbols = collect_forced_symbols(
        decision_input=decision_input,
        screener_config=screener_config,
    )

    all_symbols = {
        str(symbol).strip().upper()
        for symbol in market.keys()
        if str(symbol).strip()
    }

    all_symbols.update(forced_symbols)

    (
        asset_lookup,
        asset_warnings,
    ) = load_asset_lookup(ASSETS_PATH)

    if not asset_lookup:
        raise RuntimeError(
            "没有可用的Alpaca资产状态，"
            "无法生成第一次Codex粗选输入"
        )

    hard_filters = screener_config.get(
        "hard_filters",
        {},
    )

    evaluated: list[dict[str, Any]] = []

    for symbol in sorted(all_symbols):
        market_summary = market.get(
            symbol,
            {
                "symbol": symbol,
                "data_status": (
                    "missing_from_market_snapshot"
                ),
                "daily": None,
                "intraday": None,
            },
        )

        item = evaluate_symbol(
            symbol=symbol,
            market_summary=market_summary,
            screener_config=screener_config,
            project_root=project_root,
            forced=(
                symbol in forced_symbols
            ),
            asset_lookup=asset_lookup,
        )

        # 当前新版 evaluate_symbol 已接收 asset_lookup。
        # 若它没有在返回值中写入 asset 摘要，再补做一次。
        if not isinstance(item.get("asset"), dict):
            add_asset_validation(
                item=item,
                asset_lookup=asset_lookup,
                hard_filters=hard_filters,
            )
        else:
            normalize_screen_status(item)

        evaluated.append(item)

    return evaluated, asset_warnings


def sort_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    priority_score只作为Codex阅读顺序参考，
    不决定最终60只。
    """
    return sorted(
        entries,
        key=lambda item: (
            float(
                item.get(
                    "priority_score",
                    0.0,
                )
                or 0.0
            ),
            str(
                item.get("symbol", "")
            ),
        ),
        reverse=True,
    )


def build_coarse_universe_input() -> Path:
    """
    构建第一次Codex调用的全市场粗选输入。

    Python职责：
    - 资产状态和数据完整性硬过滤；
    - 标记风险隔离；
    - 整理轻量指标和长日线路径；
    - 明确必须检查的持仓、挂单及核心标的。

    Python不再决定最终60只。
    """
    project_root = get_project_root()

    decision_input_path = (
        project_root
        / "data"
        / "snapshots"
        / "decision_input.json"
    )

    screener_config_path = (
        project_root
        / "config"
        / "screener.json"
    )

    decision_input = load_json_file(
        decision_input_path
    )

    screener_config = load_json_file(
        screener_config_path
    )

    (
        evaluated,
        asset_warnings,
    ) = evaluate_full_universe(
        decision_input=decision_input,
        screener_config=screener_config,
    )

    eligible = sort_entries(
        [
            item
            for item in evaluated
            if item.get("screen_status")
            == "eligible"
        ]
    )

    quarantined = sort_entries(
        [
            item
            for item in evaluated
            if item.get("screen_status")
            == "quarantined"
        ]
    )

    excluded = sorted(
        [
            item
            for item in evaluated
            if item.get("screen_status")
            == "excluded"
        ],
        key=lambda item: str(
            item.get("symbol", "")
        ),
    )

    forced_entries = sorted(
        [
            item
            for item in evaluated
            if item.get(
                "forced_include",
                False,
            )
        ],
        key=lambda item: str(
            item.get("symbol", "")
        ),
    )

    required_symbols = [
        str(item.get("symbol", ""))
        for item in forced_entries
        if str(item.get("symbol", "")).strip()
    ]

    # Codex粗选时主要从eligible中选择。
    # 被隔离或排除但属于持仓、挂单或核心标的的，
    # 仍然交给Codex处理，但不得当作普通新开仓候选。
    codex_review_lookup: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in eligible + forced_entries:
        symbol = str(
            item.get("symbol", "")
        ).strip().upper()

        if not symbol:
            continue

        codex_review_lookup[symbol] = item

    codex_review_universe = sort_entries(
        list(codex_review_lookup.values())
    )

    target_count = int(
        screener_config
        .get("selection", {})
        .get("max_candidates", 60)
    )

    result = {
        "schema_version": "2.0",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "success",
        "purpose": (
            "第一次Codex全市场粗选输入。"
            "Python只执行硬过滤、风险标记和数据整理；"
            "最终60只由Codex结合长日线、联网研究、"
            "市场环境和行业平衡自主选择。"
        ),
        "run_context": decision_input.get(
            "run_context",
            {},
        ),
        "account": decision_input.get(
            "account",
            {},
        ),
        "portfolio": decision_input.get(
            "portfolio",
            {},
        ),
        "open_orders": decision_input.get(
            "open_orders",
            {},
        ),
        "selection_policy": {
            "target_candidate_count": (
                target_count
            ),
            "python_selects_final_candidates": (
                False
            ),
            "codex_selects_final_candidates": (
                True
            ),
            "required_symbols_must_be_included": (
                True
            ),
            "market_balance_required": True,
            "priority_score_is_reference_only": (
                True
            ),
            "eligible_symbols_may_be_opened": (
                True
            ),
            "quarantined_symbols_may_be_opened": (
                False
            ),
            "excluded_symbols_may_be_opened": (
                False
            ),
        },
        "selection_summary": {
            "total_evaluated": len(
                evaluated
            ),
            "eligible_count": len(
                eligible
            ),
            "quarantined_count": len(
                quarantined
            ),
            "excluded_count": len(
                excluded
            ),
            "forced_include_count": len(
                forced_entries
            ),
            "codex_review_universe_count": (
                len(codex_review_universe)
            ),
            "target_candidate_count": (
                target_count
            ),
            "verified_asset_count": len(
                evaluated
            ),
        },
        "required_symbols": (
            required_symbols
        ),
        "codex_review_universe": [
            build_compact_entry(item)
            for item in codex_review_universe
        ],
        "quarantined": [
            build_compact_entry(item)
            for item in quarantined
        ],
        "excluded": [
            build_compact_entry(item)
            for item in excluded
        ],
        "data_access": {
            "daily_bar_directory": (
                "data/raw_bars/daily/"
            ),
            "daily_bar_retention": 300,
            "codex_may_read_daily_files": (
                True
            ),
            "codex_may_create_temporary_helpers": (
                True
            ),
            "temporary_helper_directory": (
                ".tmp/codex/"
            ),
        },
        "warnings": asset_warnings,
        "notes": [
            (
                "codex_review_universe不是Python推荐名单，"
                "而是第一次Codex允许研究的完整范围。"
            ),
            (
                "priority_score_reference仅用于帮助Codex"
                "安排阅读顺序，不得直接截取前60只。"
            ),
            (
                "forced_include中的隔离或排除标的只能用于"
                "持仓、挂单和风险管理，不得因此绕过开仓限制。"
            ),
        ],
    }

    output_path = (
        project_root
        / "data"
        / "snapshots"
        / OUTPUT_FILENAME
    )

    save_json_atomically(
        output_path,
        result,
    )

    return output_path


def main() -> int:
    """单独运行时生成第一次Codex粗选输入。"""
    try:
        print(f"脚本版本：{SCRIPT_VERSION}")
        output_path = (
            build_coarse_universe_input()
        )

        result = load_json_file(
            output_path
        )

        summary = result.get(
            "selection_summary",
            {},
        )

        print("全市场粗选输入生成成功")
        print(f"保存位置：{output_path}")
        print(
            "评估总数："
            f"{summary.get('total_evaluated', 0)}"
        )
        print(
            "普通合格："
            f"{summary.get('eligible_count', 0)}"
        )
        print(
            "风险隔离："
            f"{summary.get('quarantined_count', 0)}"
        )
        print(
            "硬性排除："
            f"{summary.get('excluded_count', 0)}"
        )
        print(
            "Codex可研究范围："
            f"{summary.get('codex_review_universe_count', 0)}"
        )
        print(
            "Codex最终目标数量："
            f"{summary.get('target_candidate_count', 0)}"
        )

        return 0

    except Exception as error:
        print("全市场粗选输入生成失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())