import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fetch_account import save_json_atomically
from runtime_paths import (
    build_runtime_paths,
    find_latest_stage_workspace,
    get_project_root,
)
from validate_coarse_candidates import (
    validate_coarse_candidates,
)


SCRIPT_VERSION = (
    "2026-07-22-validation-key-compat-v2"
)

NEW_YORK_TIMEZONE = ZoneInfo(
    "America/New_York"
)

COARSE_OUTPUT_MAX_AGE_HOURS = 24
TARGET_DAILY_BARS = 300

REQUIRED_ACCOUNT_SNAPSHOTS = (
    "account.json",
    "positions.json",
    "open_orders.json",
    "today_orders.json",
    "assets.json",
)

OPTIONAL_ACCOUNT_SNAPSHOTS = (
    "decision_input.json",
)


def load_json_object(
    path: Path,
) -> dict[str, Any]:
    """读取JSON对象。"""
    if not path.exists():
        raise FileNotFoundError(
            f"缺少文件：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON顶层必须是对象：{path}"
        )

    return payload


def load_optional_json_object(
    path: Path,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:
    """读取可选JSON对象。"""
    if not path.exists():
        return None, f"缺少可选文件：{path}"

    try:
        return load_json_object(path), None
    except Exception as error:
        return None, (
            f"读取可选文件失败：{path}；{error}"
        )


def normalize_symbol(
    value: Any,
) -> str:
    """标准化股票代码。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def safe_float(
    value: Any,
) -> float | None:
    """转换为有限浮点数。"""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def parse_datetime(
    value: Any,
) -> datetime | None:
    """解析ISO时间。"""
    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def relative_path(
    path: Path,
    project_root: Path,
) -> str:
    """优先返回项目相对路径。"""
    try:
        return str(
            path.resolve().relative_to(
                project_root.resolve()
            )
        )
    except ValueError:
        return str(path.resolve())


def extract_bars(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """从标准行情快照中提取并排序K线。"""
    data = snapshot.get("data", {})

    if not isinstance(data, dict):
        return []

    bars = data.get("bars", [])

    if not isinstance(bars, list):
        return []

    valid = [
        bar
        for bar in bars
        if isinstance(bar, dict)
        and isinstance(
            bar.get("timestamp"),
            str,
        )
    ]

    valid.sort(
        key=lambda bar: bar["timestamp"]
    )

    return valid


def calculate_return(
    closes: list[float],
    periods: int,
) -> float | None:
    """计算指定交易日跨度收益。"""
    if len(closes) <= periods:
        return None

    start = closes[-periods - 1]
    end = closes[-1]

    if start == 0:
        return None

    return end / start - 1.0


def calculate_sma(
    closes: list[float],
    periods: int,
) -> float | None:
    """计算简单移动平均。"""
    if len(closes) < periods:
        return None

    return sum(
        closes[-periods:]
    ) / periods


def calculate_annualized_volatility(
    closes: list[float],
    periods: int,
) -> float | None:
    """根据日收益计算年化波动率。"""
    if len(closes) < periods + 1:
        return None

    relevant = closes[
        -(periods + 1):
    ]

    returns: list[float] = []

    for previous, current in zip(
        relevant,
        relevant[1:],
    ):
        if previous <= 0:
            continue

        returns.append(
            current / previous - 1.0
        )

    if len(returns) < 2:
        return None

    return (
        statistics.stdev(returns)
        * math.sqrt(252)
    )


def calculate_max_drawdown(
    closes: list[float],
    periods: int,
) -> float | None:
    """计算最近指定区间最大回撤。"""
    if not closes:
        return None

    relevant = closes[-periods:]

    if not relevant:
        return None

    running_peak = relevant[0]
    maximum_drawdown = 0.0

    for close in relevant:
        running_peak = max(
            running_peak,
            close,
        )

        if running_peak <= 0:
            continue

        drawdown = (
            close / running_peak - 1.0
        )

        maximum_drawdown = min(
            maximum_drawdown,
            drawdown,
        )

    return maximum_drawdown


def calculate_daily_summary(
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算第二阶段需要的日线摘要。"""
    closes = [
        close
        for close in (
            safe_float(bar.get("close"))
            for bar in bars
        )
        if close is not None
    ]

    volumes = [
        volume
        for volume in (
            safe_float(bar.get("volume"))
            for bar in bars[-20:]
        )
        if volume is not None
    ]

    latest_bar = (
        bars[-1]
        if bars
        else {}
    )

    latest_close = (
        closes[-1]
        if closes
        else None
    )

    sma20 = calculate_sma(
        closes,
        20,
    )
    sma50 = calculate_sma(
        closes,
        50,
    )
    sma200 = calculate_sma(
        closes,
        200,
    )

    def distance_from(
        average: float | None,
    ) -> float | None:
        if (
            latest_close is None
            or average is None
            or average == 0
        ):
            return None

        return (
            latest_close / average - 1.0
        )

    return {
        "bar_count": len(bars),
        "first_bar_at": (
            bars[0].get("timestamp")
            if bars
            else None
        ),
        "last_bar_at": (
            latest_bar.get("timestamp")
            if bars
            else None
        ),
        "latest_close": latest_close,
        "return_1d": calculate_return(
            closes,
            1,
        ),
        "return_5d": calculate_return(
            closes,
            5,
        ),
        "return_20d": calculate_return(
            closes,
            20,
        ),
        "return_60d": calculate_return(
            closes,
            60,
        ),
        "return_120d": calculate_return(
            closes,
            120,
        ),
        "annualized_volatility_20d": (
            calculate_annualized_volatility(
                closes,
                20,
            )
        ),
        "annualized_volatility_60d": (
            calculate_annualized_volatility(
                closes,
                60,
            )
        ),
        "max_drawdown_60d": (
            calculate_max_drawdown(
                closes,
                60,
            )
        ),
        "max_drawdown_120d": (
            calculate_max_drawdown(
                closes,
                120,
            )
        ),
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "distance_from_sma20": (
            distance_from(sma20)
        ),
        "distance_from_sma50": (
            distance_from(sma50)
        ),
        "distance_from_sma200": (
            distance_from(sma200)
        ),
        "average_volume_20d": (
            sum(volumes) / len(volumes)
            if volumes
            else None
        ),
    }


def calculate_intraday_summary(
    snapshot: dict[str, Any],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """规范化盘中摘要并区分暂无数据与失败。"""
    data = snapshot.get("data", {})

    if not isinstance(data, dict):
        data = {}

    status = snapshot.get("status")

    if status not in {
        "success",
        "no_data",
        "failed",
    }:
        status = (
            "success"
            if bars
            else "no_data"
        )

    return {
        "status": status,
        "market_date": data.get(
            "market_date"
        ),
        "window_status": data.get(
            "window_status"
        ),
        "query_start": data.get(
            "query_start"
        ),
        "query_end": data.get(
            "query_end"
        ),
        "bar_count": len(bars),
        "first_bar_at": data.get(
            "first_bar_at"
        ),
        "last_bar_at": data.get(
            "last_bar_at"
        ),
        "session_open": data.get(
            "session_open"
        ),
        "session_high": data.get(
            "session_high"
        ),
        "session_low": data.get(
            "session_low"
        ),
        "latest_close": data.get(
            "latest_close"
        ),
        "total_volume": data.get(
            "total_volume"
        ),
        "no_data_is_expected": (
            status == "no_data"
            and data.get("window_status")
            in {
                "before_delayed_data_available",
                "market_closed_weekend",
            }
        ),
        "warnings": (
            data.get("warnings", [])
            if isinstance(
                data.get("warnings", []),
                list,
            )
            else []
        ),
    }


def build_screen_lookup(
    coarse_input: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造Python粗筛原始记录索引。"""
    records = coarse_input.get(
        "codex_review_universe",
        [],
    )

    if not isinstance(records, list):
        raise ValueError(
            "codex_review_universe必须是数组"
        )

    lookup: dict[
        str,
        dict[str, Any],
    ] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if symbol:
            lookup[symbol] = record

    return lookup


def load_account_snapshots(
    project_root: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    list[str],
]:
    """读取账户、持仓、订单和资产快照。"""
    snapshot_directory = (
        project_root
        / "data"
        / "snapshots"
    )

    snapshots: dict[
        str,
        dict[str, Any],
    ] = {}

    warnings: list[str] = []

    for filename in REQUIRED_ACCOUNT_SNAPSHOTS:
        path = (
            snapshot_directory
            / filename
        )

        snapshots[
            filename.removesuffix(".json")
        ] = load_json_object(path)

    for filename in OPTIONAL_ACCOUNT_SNAPSHOTS:
        path = (
            snapshot_directory
            / filename
        )

        payload, warning = (
            load_optional_json_object(path)
        )

        if warning:
            warnings.append(warning)

        if payload is not None:
            snapshots[
                filename.removesuffix(".json")
            ] = payload

    return snapshots, warnings


def get_unique_selection_count(
    validation: dict[str, Any],
) -> int:
    """
    兼容读取粗选校验器的去重数量字段。

    当前规范字段为unique_selection_count；
    兼容旧版unique_count和duplicate_count。
    """
    selection_count = int(
        validation.get(
            "selection_count",
            0,
        )
    )

    current_value = validation.get(
        "unique_selection_count"
    )

    if current_value is not None:
        return int(current_value)

    legacy_value = validation.get(
        "unique_count"
    )

    if legacy_value is not None:
        return int(legacy_value)

    duplicate_count = validation.get(
        "duplicate_count"
    )

    if duplicate_count is not None:
        return max(
            0,
            selection_count
            - int(duplicate_count),
        )

    raise KeyError(
        "粗选校验结果缺少去重数量字段："
        "unique_selection_count"
    )


def build_portfolio_input() -> Path:
    """
    生成第二阶段组合决策统一输入。

    本文件包含：
    - 已验证的60只粗选结果；
    - Python粗筛原始记录；
    - 每只最多300根完整日线及摘要；
    - 当日完整5分钟盘中快照及摘要；
    - 账户、持仓、订单和资产快照；
    - 联网与执行权限边界。
    """
    project_root = get_project_root()

    coarse_workspace = (
        find_latest_stage_workspace(
            "coarse_selection",
            project_root=project_root,
        )
    )

    validation = validate_coarse_candidates(
        workspace=coarse_workspace,
        max_age_hours=(
            COARSE_OUTPUT_MAX_AGE_HOURS
        ),
    )

    if not validation["valid"]:
        raise RuntimeError(
            "粗选结果未通过Python校验，"
            "不能生成第二阶段输入：\n- "
            + "\n- ".join(
                validation["errors"]
            )
        )

    run_date = coarse_workspace.parent.name

    runtime_paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    coarse_output_path = (
        coarse_workspace
        / "output"
        / "coarse_candidates.json"
    )

    coarse_input_path = (
        coarse_workspace
        / "data"
        / "snapshots"
        / "coarse_universe_input.json"
    )

    coarse_output = load_json_object(
        coarse_output_path
    )
    coarse_input = load_json_object(
        coarse_input_path
    )

    screen_lookup = build_screen_lookup(
        coarse_input
    )

    selected = coarse_output.get(
        "selected",
        [],
    )

    if not isinstance(selected, list):
        raise ValueError(
            "coarse_candidates.selected"
            "必须是数组"
        )

    if len(selected) != 60:
        raise ValueError(
            "第二阶段输入要求恰好60只候选，"
            f"实际={len(selected)}"
        )

    account_snapshots, warnings = (
        load_account_snapshots(
            project_root
        )
    )

    candidate_records: list[
        dict[str, Any]
    ] = []

    daily_complete_count = 0
    intraday_success_count = 0
    intraday_no_data_count = 0
    intraday_failed_count = 0

    global_window_statuses: set[str] = set()

    for rank, selection in enumerate(
        selected,
        start=1,
    ):
        if not isinstance(selection, dict):
            raise ValueError(
                f"selected[{rank - 1}]必须是对象"
            )

        symbol = normalize_symbol(
            selection.get("symbol")
        )

        if not symbol:
            raise ValueError(
                f"selected[{rank - 1}]"
                "缺少symbol"
            )

        screen_record = (
            screen_lookup.get(symbol)
        )

        if screen_record is None:
            raise ValueError(
                f"{symbol}缺少Python粗筛记录"
            )

        daily_path = (
            project_root
            / "data"
            / "bars"
            / "daily"
            / f"{symbol}.json"
        )

        daily_snapshot = load_json_object(
            daily_path
        )
        daily_bars = extract_bars(
            daily_snapshot
        )

        if len(daily_bars) < TARGET_DAILY_BARS:
            raise ValueError(
                f"{symbol}日线不足"
                f"{TARGET_DAILY_BARS}根："
                f"{len(daily_bars)}"
            )

        daily_bars = daily_bars[
            -TARGET_DAILY_BARS:
        ]
        daily_complete_count += 1

        intraday_path = (
            project_root
            / "data"
            / "bars"
            / "intraday"
            / run_date
            / f"{symbol}.json"
        )

        if not intraday_path.exists():
            raise FileNotFoundError(
                f"{symbol}缺少当日盘中快照："
                f"{intraday_path}；"
                "请先运行fetch_intraday_bars.py"
            )

        intraday_snapshot = (
            load_json_object(
                intraday_path
            )
        )
        intraday_bars = extract_bars(
            intraday_snapshot
        )
        intraday_summary = (
            calculate_intraday_summary(
                intraday_snapshot,
                intraday_bars,
            )
        )

        intraday_status = (
            intraday_summary["status"]
        )

        if intraday_status == "success":
            intraday_success_count += 1
        elif intraday_status == "no_data":
            intraday_no_data_count += 1
        else:
            intraday_failed_count += 1

        window_status = (
            intraday_summary.get(
                "window_status"
            )
        )

        if isinstance(window_status, str):
            global_window_statuses.add(
                window_status
            )

        candidate_records.append(
            {
                "rank": rank,
                "symbol": symbol,
                "coarse_selection": selection,
                "python_screen": {
                    "screen_status": (
                        screen_record.get(
                            "screen_status"
                        )
                    ),
                    "forced_include": bool(
                        screen_record.get(
                            "forced_include",
                            False,
                        )
                    ),
                    "metrics": (
                        screen_record.get(
                            "metrics",
                            {},
                        )
                    ),
                    "asset": (
                        screen_record.get(
                            "asset",
                            {},
                        )
                    ),
                    "hard_filter_reasons": (
                        screen_record.get(
                            "hard_filter_reasons",
                            [],
                        )
                    ),
                    "risk_filter_reasons": (
                        screen_record.get(
                            "risk_filter_reasons",
                            [],
                        )
                    ),
                    "warnings": (
                        screen_record.get(
                            "warnings",
                            [],
                        )
                    ),
                },
                "daily": {
                    "source_path": (
                        relative_path(
                            daily_path,
                            project_root,
                        )
                    ),
                    "snapshot_generated_at": (
                        daily_snapshot.get(
                            "generated_at"
                        )
                    ),
                    "summary": (
                        calculate_daily_summary(
                            daily_bars
                        )
                    ),
                    "bars": daily_bars,
                },
                "intraday": {
                    "source_path": (
                        relative_path(
                            intraday_path,
                            project_root,
                        )
                    ),
                    "snapshot_generated_at": (
                        intraday_snapshot.get(
                            "generated_at"
                        )
                    ),
                    "summary": (
                        intraday_summary
                    ),
                    "bars": intraday_bars,
                },
            }
        )

    coarse_network = (
        coarse_output.get(
            "network_research",
            {}
        )
    )

    if not isinstance(
        coarse_network,
        dict,
    ):
        coarse_network = {}

    generated_at_utc = datetime.now(
        timezone.utc
    )

    generated_at_new_york = (
        generated_at_utc.astimezone(
            NEW_YORK_TIMEZONE
        )
    )

    payload = {
        "schema_version": "1.0",
        "stage": "portfolio_decision_input",
        "generated_at": (
            generated_at_utc.isoformat()
        ),
        "generated_at_new_york": (
            generated_at_new_york.isoformat()
        ),
        "run_date": run_date,
        "market_timezone": (
            "America/New_York"
        ),
        "source_contract": {
            "coarse_workspace": (
                relative_path(
                    coarse_workspace,
                    project_root,
                )
            ),
            "coarse_output": (
                relative_path(
                    coarse_output_path,
                    project_root,
                )
            ),
            "coarse_input": (
                relative_path(
                    coarse_input_path,
                    project_root,
                )
            ),
            "coarse_validation": {
                "valid": True,
                "selection_count": (
                    validation[
                        "selection_count"
                    ]
                ),
                "unique_selection_count": (
                    get_unique_selection_count(
                        validation
                    )
                ),
                "required_symbol_count": (
                    validation[
                        "required_symbol_count"
                    ]
                ),
            },
        },
        "market_data_status": {
            "candidate_count": len(
                candidate_records
            ),
            "daily_complete_count": (
                daily_complete_count
            ),
            "target_daily_bars_per_symbol": (
                TARGET_DAILY_BARS
            ),
            "intraday_success_count": (
                intraday_success_count
            ),
            "intraday_no_data_count": (
                intraday_no_data_count
            ),
            "intraday_failed_count": (
                intraday_failed_count
            ),
            "intraday_window_statuses": (
                sorted(
                    global_window_statuses
                )
            ),
            "intraday_no_data_policy": (
                "no_data is acceptable before "
                "delayed SIP data becomes available "
                "or when the market is closed; it "
                "must not be interpreted as zero "
                "volume, zero price, or a trading "
                "anomaly"
            ),
        },
        "network_research_policy": {
            "coarse_stage_status": (
                coarse_network.get(
                    "status"
                )
            ),
            "portfolio_stage_must_attempt_web": True,
            "portfolio_local_only_result_allowed": True,
            "local_only_may_manage_existing_positions": True,
            "local_only_may_propose_new_positions": False,
            "must_not_invent_sources": True,
        },
        "authority_policy": {
            "portfolio_codex_may": [
                "analyze all 60 candidates",
                "propose target portfolio weights",
                "propose adds, reductions, exits, "
                "holds and protection",
                "propose risk controls and "
                "conditional triggers",
            ],
            "portfolio_codex_may_not": [
                "submit orders",
                "grant final opening permission",
                "access credentials",
                "modify production source",
                "treat screen eligibility as "
                "execution authorization",
            ],
            "python_is_final_order_authority": True,
            "execution_new_position_allowed": (
                "computed only after execution "
                "review and final Python pre-order "
                "checks"
            ),
        },
        "account_context": (
            account_snapshots
        ),
        "coarse_market_view": (
            coarse_output.get(
                "market_view"
            )
        ),
        "coarse_concentration_assessment": (
            coarse_output.get(
                "concentration_assessment"
            )
        ),
        "coarse_warnings": (
            coarse_output.get(
                "warnings",
                [],
            )
        ),
        "candidates": candidate_records,
        "warnings": warnings,
    }

    output_path = (
        project_root
        / "data"
        / "snapshots"
        / "portfolio_input.json"
    )

    save_json_atomically(
        output_path,
        payload,
    )

    return output_path


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    try:
        output_path = (
            build_portfolio_input()
        )

        payload = load_json_object(
            output_path
        )

        status = payload[
            "market_data_status"
        ]

        print("第二阶段统一输入生成成功")
        print(f"输出：{output_path}")
        print(
            "候选数量："
            f"{status['candidate_count']}"
        )
        print(
            "日线完整数量："
            f"{status['daily_complete_count']}"
        )
        print(
            "盘中有数据数量："
            f"{status['intraday_success_count']}"
        )
        print(
            "盘中暂无数据数量："
            f"{status['intraday_no_data_count']}"
        )
        print(
            "盘中失败数量："
            f"{status['intraday_failed_count']}"
        )
        print(
            "盘中窗口状态："
            + ", ".join(
                status[
                    "intraday_window_statuses"
                ]
            )
        )

        return 0

    except Exception as error:
        print("第二阶段统一输入生成失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())