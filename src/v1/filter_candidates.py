import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_project_root
from fetch_account import save_json_atomically


PROJECT_ROOT = get_project_root()

DECISION_INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "snapshots"
    / "decision_input.json"
)

ASSETS_PATH = (
    PROJECT_ROOT
    / "data"
    / "snapshots"
    / "assets.json"
)

SCREENER_CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "screener.json"
)

OUTPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "snapshots"
    / "candidate_input.json"
)


# -----------------------------------------------------------------------------
# 通用辅助函数
# -----------------------------------------------------------------------------


def load_json_file(file_path: Path) -> dict[str, Any]:
    """读取 JSON 文件，并确认顶层结构是对象。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"没有找到所需文件：{file_path}"
        )

    with file_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        content = json.load(file)

    if not isinstance(content, dict):
        raise ValueError(
            f"JSON文件顶层必须是对象：{file_path}"
        )

    return content


def normalize_symbol(value: Any) -> str:
    """将股票或 ETF 代码标准化为大写字符串。"""
    if value is None:
        return ""

    return str(value).strip().upper()


def safe_float(value: Any) -> float | None:
    """将字符串或数字安全转换为有限浮点数。"""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def parse_iso_datetime(value: Any) -> datetime | None:
    """将 ISO 8601 字符串转换为带时区的 datetime。"""
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


def clip(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    """将数值限制在指定区间内。"""
    return max(minimum, min(value, maximum))


# -----------------------------------------------------------------------------
# Alpaca 资产状态
# -----------------------------------------------------------------------------


def load_asset_lookup(
    assets_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    list[str],
]:
    """
    读取 Alpaca 资产状态，并根据 symbol 建立索引。

    返回：
    - symbol -> asset 字典
    - 全局数据警告列表
    """
    warnings: list[str] = []

    if not assets_path.exists():
        warnings.append(
            "缺少 assets.json，无法核验 active 和 tradable 状态"
        )
        return {}, warnings

    try:
        payload = load_json_file(assets_path)
    except Exception as error:
        warnings.append(
            "assets.json 读取失败："
            f"{error}"
        )
        return {}, warnings

    if payload.get("status") != "success":
        warnings.append(
            "assets.json 状态不是 success"
        )

    asset_records = (
        payload
        .get("data", {})
        .get("assets", [])
    )

    if not isinstance(asset_records, list):
        warnings.append(
            "assets.json 中的 assets 不是数组"
        )
        return {}, warnings

    lookup: dict[str, dict[str, Any]] = {}

    for record in asset_records:
        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

        if symbol in lookup:
            warnings.append(
                f"assets.json 中存在重复标的：{symbol}"
            )

        lookup[symbol] = record

    if not lookup:
        warnings.append(
            "assets.json 未提供任何有效资产记录"
        )

    return lookup, warnings


def evaluate_asset_eligibility(
    symbol: str,
    asset_lookup: dict[
        str,
        dict[str, Any],
    ],
    hard_filters: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[str],
]:
    """根据 Alpaca 资产状态生成硬性排除原因。"""
    asset = asset_lookup.get(symbol)

    if asset is None:
        return (
            {
                "found": False,
                "asset_class": None,
                "status": None,
                "exchange": None,
                "tradable": None,
                "fractionable": None,
                "is_active": False,
                "is_us_equity": False,
                "eligible_for_v1": False,
            },
            ["asset_status_missing"],
        )

    found = asset.get("found") is True
    asset_class = asset.get("asset_class")
    status = asset.get("status")
    tradable = asset.get("tradable")

    asset_summary = {
        "found": found,
        "asset_class": asset_class,
        "status": status,
        "exchange": asset.get("exchange"),
        "tradable": tradable,
        "fractionable": asset.get(
            "fractionable"
        ),
        "is_active": status == "active",
        "is_us_equity": (
            asset_class == "us_equity"
        ),
        "eligible_for_v1": (
            found
            and asset_class == "us_equity"
            and status == "active"
            and tradable is True
        ),
    }

    exclusion_reasons: list[str] = []

    if not found:
        exclusion_reasons.append(
            "asset_not_found_in_alpaca"
        )

    # 股票和 ETF 在 Alpaca 中都属于 US_EQUITY。
    if asset_class != "us_equity":
        exclusion_reasons.append(
            "asset_class_not_us_equity"
        )

    if (
        hard_filters.get(
            "require_active",
            True,
        )
        and status != "active"
    ):
        exclusion_reasons.append(
            "asset_not_active"
        )

    if (
        hard_filters.get(
            "require_tradable",
            True,
        )
        and tradable is not True
    ):
        exclusion_reasons.append(
            "asset_not_tradable"
        )

    # 保持原因唯一，避免一个缺失资产同时产生重复原因。
    exclusion_reasons = list(
        dict.fromkeys(exclusion_reasons)
    )

    return asset_summary, exclusion_reasons


# -----------------------------------------------------------------------------
# 行情指标
# -----------------------------------------------------------------------------


def load_daily_bars(
    project_root: Path,
    symbol: str,
) -> list[dict[str, Any]]:
    """读取单个标的的原始日线。"""
    file_path = (
        project_root
        / "data"
        / "bars"
        / "daily"
        / f"{symbol}.json"
    )

    if not file_path.exists():
        return []

    snapshot = load_json_file(file_path)

    data = snapshot.get("data", {})
    bars = data.get("bars", [])

    if not isinstance(bars, list):
        return []

    valid_bars = [
        bar
        for bar in bars
        if isinstance(bar, dict)
    ]

    valid_bars.sort(
        key=lambda bar: str(
            bar.get("timestamp", "")
        )
    )

    return valid_bars


def calculate_average_dollar_volume(
    bars: list[dict[str, Any]],
    periods: int = 20,
) -> float | None:
    """
    计算最近若干日的平均成交金额。

    近似计算方式：收盘价 × 成交量。
    """
    if len(bars) < periods:
        return None

    dollar_volumes: list[float] = []

    for bar in bars[-periods:]:
        close = safe_float(bar.get("close"))
        volume = safe_float(
            bar.get("volume")
        )

        if (
            close is None
            or volume is None
            or close <= 0
            or volume < 0
        ):
            continue

        dollar_volumes.append(
            close * volume
        )

    if len(dollar_volumes) < periods:
        return None

    return sum(dollar_volumes) / len(
        dollar_volumes
    )


def calculate_latest_gap(
    daily_bars: list[dict[str, Any]],
    intraday_summary: dict[str, Any] | None,
) -> float | None:
    """
    计算当天开盘价相对上一交易日收盘价的跳空幅度。

    优先使用盘中第一根 K 线的开盘价。
    """
    if not intraday_summary:
        return None

    session_open = safe_float(
        intraday_summary.get("session_open")
    )

    market_date = intraday_summary.get(
        "market_date"
    )

    if (
        session_open is None
        or not isinstance(market_date, str)
        or not market_date
    ):
        return None

    previous_close: float | None = None

    for bar in reversed(daily_bars):
        timestamp = parse_iso_datetime(
            bar.get("timestamp")
        )
        close = safe_float(bar.get("close"))

        if timestamp is None or close is None:
            continue

        if timestamp.date().isoformat() < market_date:
            previous_close = close
            break

    if (
        previous_close is None
        or previous_close <= 0
    ):
        return None

    return session_open / previous_close - 1


def calculate_priority_score(
    daily_summary: dict[str, Any],
) -> float:
    """
    计算候选标的的阅读优先级。

    该分数只决定 Codex 优先阅读哪些标的，
    不构成买入信号。
    """
    score = 0.0

    if daily_summary.get("price_above_ma20"):
        score += 1.0

    if daily_summary.get("price_above_ma50"):
        score += 1.0

    return_5d = safe_float(
        daily_summary.get("return_5d")
    )

    if return_5d is not None:
        score += 0.5 * clip(
            return_5d / 0.10,
            -1.0,
            1.0,
        )

    return_20d = safe_float(
        daily_summary.get("return_20d")
    )

    if return_20d is not None:
        score += clip(
            return_20d / 0.20,
            -1.0,
            1.0,
        )

    volatility = safe_float(
        daily_summary.get(
            "volatility_20d_annualized"
        )
    )

    if volatility is not None:
        score -= 0.5 * clip(
            volatility / 1.0,
            0.0,
            1.0,
        )

    return round(score, 6)


# -----------------------------------------------------------------------------
# 强制标的与单标的筛选
# -----------------------------------------------------------------------------


def collect_forced_symbols(
    decision_input: dict[str, Any],
    screener_config: dict[str, Any],
) -> set[str]:
    """
    收集无论筛选结果如何都必须交给 Codex 查看 的标的。

    包括：
    - 配置中的基准和防御 ETF
    - 当前持仓
    - 未完成订单涉及的标的
    """
    selection_config = screener_config.get(
        "selection",
        {},
    )

    forced_symbols = {
        normalize_symbol(symbol)
        for symbol in selection_config.get(
            "always_include",
            [],
        )
        if normalize_symbol(symbol)
    }

    if selection_config.get(
        "include_current_positions",
        True,
    ):
        positions = (
            decision_input
            .get("portfolio", {})
            .get("positions", [])
        )

        if isinstance(positions, list):
            for position in positions:
                if not isinstance(position, dict):
                    continue

                symbol = normalize_symbol(
                    position.get("symbol")
                )

                if symbol:
                    forced_symbols.add(symbol)

    if selection_config.get(
        "include_open_order_symbols",
        True,
    ):
        orders = (
            decision_input
            .get("open_orders", {})
            .get("orders", [])
        )

        if isinstance(orders, list):
            for order in orders:
                if not isinstance(order, dict):
                    continue

                symbol = normalize_symbol(
                    order.get("symbol")
                )

                if symbol:
                    forced_symbols.add(symbol)

    return forced_symbols


def evaluate_symbol(
    symbol: str,
    market_summary: dict[str, Any],
    screener_config: dict[str, Any],
    project_root: Path,
    asset_lookup: dict[
        str,
        dict[str, Any],
    ],
    forced: bool,
) -> dict[str, Any]:
    """
    对单个标的执行资产资格、硬过滤和风险过滤。

    返回状态：
    - eligible
    - quarantined
    - excluded
    """
    hard_config = screener_config.get(
        "hard_filters",
        {},
    )

    risk_config = screener_config.get(
        "risk_filters",
        {},
    )

    hard_reasons: list[str] = []
    risk_reasons: list[str] = []
    warnings: list[str] = []

    (
        asset_summary,
        asset_filter_reasons,
    ) = evaluate_asset_eligibility(
        symbol=symbol,
        asset_lookup=asset_lookup,
        hard_filters=hard_config,
    )

    hard_reasons.extend(
        asset_filter_reasons
    )

    data_status = market_summary.get(
        "data_status"
    )

    daily_summary = (
        market_summary.get("daily")
        if isinstance(
            market_summary.get("daily"),
            dict,
        )
        else {}
    )

    intraday_summary = (
        market_summary.get("intraday")
        if isinstance(
            market_summary.get("intraday"),
            dict,
        )
        else {}
    )

    daily_bars = load_daily_bars(
        project_root=project_root,
        symbol=symbol,
    )

    bar_count = len(daily_bars)

    minimum_history = int(
        hard_config.get(
            "min_history_bars",
            120,
        )
    )

    if data_status != "available":
        hard_reasons.append(
            f"市场数据状态异常：{data_status}"
        )

    if bar_count < minimum_history:
        hard_reasons.append(
            f"日线数量不足：{bar_count} < "
            f"{minimum_history}"
        )

    latest_bar_at = daily_summary.get(
        "latest_bar_at"
    )

    latest_bar_time = parse_iso_datetime(
        latest_bar_at
    )

    max_data_age = int(
        hard_config.get(
            "max_data_age_calendar_days",
            5,
        )
    )

    data_age_days: float | None = None

    if latest_bar_time is None:
        hard_reasons.append(
            "无法识别最新日线时间"
        )
    else:
        now_utc = datetime.now(timezone.utc)

        data_age_days = (
            now_utc
            - latest_bar_time.astimezone(
                timezone.utc
            )
        ).total_seconds() / 86400

        if data_age_days > max_data_age:
            hard_reasons.append(
                "日线数据过期："
                f"{data_age_days:.2f}天"
            )

    latest_price = safe_float(
        daily_summary.get("latest_close")
    )

    if (
        latest_price is None
        or latest_price <= 0
    ):
        hard_reasons.append(
            "缺少有效的最新价格"
        )

    average_dollar_volume = (
        calculate_average_dollar_volume(
            daily_bars,
            periods=20,
        )
    )

    latest_gap = calculate_latest_gap(
        daily_bars=daily_bars,
        intraday_summary=intraday_summary,
    )

    min_price = safe_float(
        risk_config.get("min_price")
    )

    if (
        min_price is not None
        and latest_price is not None
        and latest_price < min_price
    ):
        risk_reasons.append(
            "价格低于最低要求："
            f"{latest_price:.2f} < {min_price:.2f}"
        )

    min_dollar_volume = safe_float(
        risk_config.get(
            "min_average_dollar_volume_20d"
        )
    )

    if average_dollar_volume is None:
        risk_reasons.append(
            "无法计算20日平均成交金额"
        )
    elif (
        min_dollar_volume is not None
        and average_dollar_volume
        < min_dollar_volume
    ):
        risk_reasons.append(
            "20日平均成交金额过低："
            f"{average_dollar_volume:.2f}"
        )

    volatility = safe_float(
        daily_summary.get(
            "volatility_20d_annualized"
        )
    )

    max_volatility = safe_float(
        risk_config.get(
            "max_annualized_volatility_20d"
        )
    )

    if (
        volatility is not None
        and max_volatility is not None
        and volatility > max_volatility
    ):
        risk_reasons.append(
            "20日年化波动率过高："
            f"{volatility:.4f}"
        )

    return_1d = safe_float(
        daily_summary.get("return_1d")
    )

    max_return_1d = safe_float(
        risk_config.get(
            "max_absolute_return_1d"
        )
    )

    if (
        return_1d is not None
        and max_return_1d is not None
        and abs(return_1d) > max_return_1d
    ):
        risk_reasons.append(
            "单日涨跌幅异常："
            f"{return_1d:.4f}"
        )

    max_gap = safe_float(
        risk_config.get(
            "max_absolute_gap"
        )
    )

    if (
        latest_gap is not None
        and max_gap is not None
        and abs(latest_gap) > max_gap
    ):
        risk_reasons.append(
            "开盘跳空幅度异常："
            f"{latest_gap:.4f}"
        )

    hard_reasons = list(
        dict.fromkeys(hard_reasons)
    )
    risk_reasons = list(
        dict.fromkeys(risk_reasons)
    )

    if hard_reasons:
        screen_status = "excluded"
    elif risk_reasons:
        screen_status = "quarantined"
    else:
        screen_status = "eligible"

    new_position_allowed = (
        screen_status == "eligible"
    )
    increase_allowed = (
        screen_status == "eligible"
    )

    if forced and screen_status != "eligible":
        warnings.append(
            "该标的是强制检查对象，但不得新开仓或加仓"
        )

    return {
        "symbol": symbol,
        # status 方便提示词直接读取；screen_status 保留旧兼容。
        "status": screen_status,
        "screen_status": screen_status,
        "forced_include": forced,
        "new_position_allowed": (
            new_position_allowed
        ),
        "increase_allowed": increase_allowed,
        "priority_score": (
            calculate_priority_score(
                daily_summary
            )
        ),
        "asset": asset_summary,
        "metrics": {
            "latest_price": (
                round(latest_price, 6)
                if latest_price is not None
                else None
            ),
            "history_bar_count": bar_count,
            "data_age_calendar_days": (
                round(data_age_days, 4)
                if data_age_days is not None
                else None
            ),
            "average_dollar_volume_20d": (
                round(
                    average_dollar_volume,
                    2,
                )
                if average_dollar_volume
                is not None
                else None
            ),
            "return_1d": return_1d,
            "return_5d": safe_float(
                daily_summary.get("return_5d")
            ),
            "return_20d": safe_float(
                daily_summary.get("return_20d")
            ),
            "volatility_20d_annualized": (
                volatility
            ),
            "latest_gap": (
                round(latest_gap, 6)
                if latest_gap is not None
                else None
            ),
            "price_above_ma20": (
                daily_summary.get(
                    "price_above_ma20"
                )
            ),
            "price_above_ma50": (
                daily_summary.get(
                    "price_above_ma50"
                )
            ),
        },
        "asset_filter_reasons": (
            asset_filter_reasons
        ),
        "hard_filter_reasons": hard_reasons,
        "risk_filter_reasons": risk_reasons,
        "warnings": warnings,
        "market_summary": market_summary,
    }


# -----------------------------------------------------------------------------
# 候选文件构建
# -----------------------------------------------------------------------------


def build_candidate_input() -> Path:
    """构建供 Codex 读取的候选标的文件。"""
    decision_input = load_json_file(
        DECISION_INPUT_PATH
    )

    screener_config = load_json_file(
        SCREENER_CONFIG_PATH
    )

    asset_lookup, asset_warnings = (
        load_asset_lookup(ASSETS_PATH)
    )

    market_raw = decision_input.get(
        "market",
        {},
    )

    if not isinstance(market_raw, dict):
        raise ValueError(
            "decision_input.json中的market必须是对象"
        )

    # 标准化 market 的 key，避免大小写造成重复或遗漏。
    market: dict[str, dict[str, Any]] = {}

    for raw_symbol, raw_summary in market_raw.items():
        symbol = normalize_symbol(raw_symbol)

        if not symbol:
            continue

        if isinstance(raw_summary, dict):
            market[symbol] = raw_summary
        else:
            market[symbol] = {
                "symbol": symbol,
                "data_status": "invalid_market_summary",
                "daily": None,
                "intraday": None,
            }

    forced_symbols = collect_forced_symbols(
        decision_input=decision_input,
        screener_config=screener_config,
    )

    all_symbols = set(market.keys())
    all_symbols.update(forced_symbols)

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

        result = evaluate_symbol(
            symbol=symbol,
            market_summary=market_summary,
            screener_config=screener_config,
            project_root=PROJECT_ROOT,
            asset_lookup=asset_lookup,
            forced=symbol in forced_symbols,
        )

        evaluated.append(result)

    eligible = [
        item
        for item in evaluated
        if item["screen_status"] == "eligible"
    ]

    quarantined = [
        item
        for item in evaluated
        if item["screen_status"]
        == "quarantined"
    ]

    excluded = [
        item
        for item in evaluated
        if item["screen_status"] == "excluded"
    ]

    eligible.sort(
        key=lambda item: (
            item["priority_score"],
            item["symbol"],
        ),
        reverse=True,
    )

    quarantined.sort(
        key=lambda item: (
            item["forced_include"],
            item["priority_score"],
            item["symbol"],
        ),
        reverse=True,
    )

    excluded.sort(
        key=lambda item: (
            item["forced_include"],
            item["symbol"],
        ),
        reverse=True,
    )

    forced_entries = [
        item
        for item in evaluated
        if item["forced_include"]
    ]

    forced_entries.sort(
        key=lambda item: item["symbol"]
    )

    max_candidates = int(
        screener_config
        .get("selection", {})
        .get("max_candidates", 60)
    )

    selected_symbols: set[str] = set()
    selected_for_codex: list[
        dict[str, Any]
    ] = []

    # 强制标的即使被隔离或排除，也必须进入 Codex 视野。
    for item in forced_entries:
        if item["symbol"] in selected_symbols:
            continue

        selected_for_codex.append(item)
        selected_symbols.add(item["symbol"])

    remaining_slots = max(
        0,
        max_candidates
        - len(selected_for_codex),
    )

    # 剩余名额只加入 eligible 标的。
    for item in eligible:
        if remaining_slots <= 0:
            break

        if item["symbol"] in selected_symbols:
            continue

        selected_for_codex.append(item)
        selected_symbols.add(item["symbol"])
        remaining_slots -= 1

    global_warnings = list(
        dict.fromkeys(asset_warnings)
    )

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "success",
        "purpose": (
            "Python初筛结果，仅用于缩小Codex阅读范围，"
            "不构成买入或卖出建议。"
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
        "data_quality": {
            "asset_snapshot_path": str(
                ASSETS_PATH.relative_to(
                    PROJECT_ROOT
                )
            ),
            "asset_lookup_count": len(
                asset_lookup
            ),
            "asset_warnings": global_warnings,
        },
        "source_files": {
            "decision_input": str(
                DECISION_INPUT_PATH.relative_to(
                    PROJECT_ROOT
                )
            ),
            "assets": str(
                ASSETS_PATH.relative_to(
                    PROJECT_ROOT
                )
            ),
            "screener_config": str(
                SCREENER_CONFIG_PATH.relative_to(
                    PROJECT_ROOT
                )
            ),
        },
        "selection_summary": {
            "total_evaluated": len(evaluated),
            "eligible_count": len(eligible),
            "quarantined_count": len(
                quarantined
            ),
            "excluded_count": len(excluded),
            "forced_include_count": len(
                forced_entries
            ),
            "selected_for_codex_count": len(
                selected_for_codex
            ),
            "configured_max_candidates": (
                max_candidates
            ),
            "forced_count_exceeds_max": (
                len(forced_entries)
                > max_candidates
            ),
        },
        "selected_for_codex": (
            selected_for_codex
        ),
        "eligible": eligible,
        "quarantined": quarantined,
        "excluded": excluded,
        "notes": [
            (
                "forced_include标的即使被隔离或排除，"
                "仍会提供给Codex，以便处理已有持仓"
                "和未完成订单。"
            ),
            (
                "eligible标的允许新开仓或加仓；"
                "quarantined和excluded标的默认禁止"
                "新开仓与加仓。"
            ),
            (
                "priority_score只用于排列阅读顺序，"
                "不能直接作为交易信号。"
            ),
            (
                "Alpaca资产资格已接入：必须属于"
                "us_equity，并按配置检查active和tradable。"
            ),
        ],
    }

    save_json_atomically(
        OUTPUT_PATH,
        result,
    )

    return OUTPUT_PATH


def main() -> int:
    """单独运行本文件时生成候选标的快照。"""
    try:
        output_path = build_candidate_input()

        print("候选标的初筛完成")
        print(f"保存位置：{output_path}")

        result = load_json_file(output_path)

        summary = result.get(
            "selection_summary",
            {},
        )

        data_quality = result.get(
            "data_quality",
            {},
        )

        print(
            "评估总数："
            f"{summary.get('total_evaluated', 0)}"
        )
        print(
            "普通候选："
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
            "强制检查："
            f"{summary.get('forced_include_count', 0)}"
        )
        print(
            "提供给Codex："
            f"{summary.get('selected_for_codex_count', 0)}"
        )
        print(
            "已核验资产数量："
            f"{data_quality.get('asset_lookup_count', 0)}"
        )

        asset_warnings = data_quality.get(
            "asset_warnings",
            [],
        )

        if asset_warnings:
            print("资产状态警告：")

            for warning in asset_warnings:
                print(f"- {warning}")

        return 0

    except Exception as error:
        print("候选标的初筛失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())