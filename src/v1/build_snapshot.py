import json
import math
import statistics
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import get_project_root, load_symbols
from fetch_account import save_json_atomically


NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")
"""
最新日线价格
1日收益率
5日收益率
20日收益率
20日均线
50日均线
价格是否高于均线
20日年化波动率
当天开盘以来涨跌
最新盘中价格
原始数据文件位置"""

def load_json_file(file_path: Path) -> dict[str, Any]:
    """读取 JSON 文件并返回字典。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"没有找到所需文件：{file_path}"
        )

    with file_path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    if not isinstance(content, dict):
        raise ValueError(
            f"JSON 文件顶层必须是对象：{file_path}"
        )

    return content


def safe_float(value: Any) -> float | None:
    """
    尝试将 Alpaca 返回的字符串或数字转换为 float。

    无法转换时返回 None。
    """
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def round_optional(
    value: float | None,
    digits: int = 6,
) -> float | None:
    """对可选浮点数进行四舍五入。"""
    if value is None:
        return None

    return round(value, digits)


def calculate_return(
    closes: list[float],
    periods: int,
) -> float | None:
    """
    计算指定周期的价格收益率。

    例如：
    periods=1 表示最近一个交易日收益率。
    periods=5 表示最近五个交易日收益率。
    """
    if len(closes) <= periods:
        return None

    previous_close = closes[-periods - 1]
    latest_close = closes[-1]

    if previous_close == 0:
        return None

    return latest_close / previous_close - 1


def calculate_moving_average(
    closes: list[float],
    periods: int,
) -> float | None:
    """计算指定周期的简单移动平均线。"""
    if len(closes) < periods:
        return None

    values = closes[-periods:]

    return sum(values) / periods


def calculate_annualized_volatility(
    closes: list[float],
    periods: int = 20,
) -> float | None:
    """
    根据最近若干交易日收益率计算年化历史波动率。

    年化系数使用 sqrt(252)。
    """
    required_close_count = periods + 1

    if len(closes) < required_close_count:
        return None

    selected_closes = closes[-required_close_count:]

    daily_returns: list[float] = []

    for index in range(1, len(selected_closes)):
        previous_close = selected_closes[index - 1]
        current_close = selected_closes[index]

        if previous_close == 0:
            continue

        daily_return = (
            current_close / previous_close - 1
        )

        daily_returns.append(daily_return)

    if len(daily_returns) < 2:
        return None

    daily_volatility = statistics.stdev(
        daily_returns
    )

    return daily_volatility * math.sqrt(252)


def determine_run_mode(
    now_new_york: datetime,
) -> str:
    """
    根据纽约时间判断本次运行所处阶段。

    这里只用于给 Codex 提供上下文，不控制是否下单。
    """
    if now_new_york.weekday() >= 5:
        return "market_closed_weekend"

    current_time = now_new_york.time()

    if current_time < time(hour=9, minute=30):
        return "before_market_open"

    if current_time < time(hour=16):
        return "regular_session"

    return "after_market_close"


def find_latest_intraday_directory(
    project_root: Path,
) -> Path | None:
    """
    找到最近一个盘中数据日期目录。

    例如：
    data/bars/intraday/2026-07-13
    """
    intraday_root = (
        project_root
        / "data"
        / "bars"
        / "intraday"
    )

    if not intraday_root.exists():
        return None

    date_directories = [
        path
        for path in intraday_root.iterdir()
        if path.is_dir()
    ]

    if not date_directories:
        return None

    date_directories.sort(
        key=lambda path: path.name,
        reverse=True,
    )

    return date_directories[0]


def build_account_summary(
    account_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """提取 Codex 决策需要的账户字段。"""
    account_data = account_snapshot.get("data", {})

    wanted_fields = [
        "status",
        "currency",
        "cash",
        "equity",
        "last_equity",
        "buying_power",
        "non_marginable_buying_power",
        "long_market_value",
        "short_market_value",
        "trading_blocked",
        "transfers_blocked",
        "account_blocked",
        "trade_suspended_by_user",
        "multiplier",
        "shorting_enabled",
    ]

    return {
        field: account_data.get(field)
        for field in wanted_fields
    }


def build_symbol_summary(
    symbol: str,
    project_root: Path,
    latest_intraday_directory: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    """
    为一个标的生成决策摘要。

    返回：
        标的摘要
        数据警告列表
    """
    warnings: list[str] = []

    daily_path = (
        project_root
        / "data"
        / "bars"
        / "daily"
        / f"{symbol}.json"
    )

    summary: dict[str, Any] = {
        "symbol": symbol,
        "data_status": "available",
        "daily": None,
        "intraday": None,
        "raw_files": {
            "daily": str(
                daily_path.relative_to(project_root)
            ),
            "intraday": None,
        },
    }

    if not daily_path.exists():
        summary["data_status"] = "missing_daily_data"
        warnings.append(
            f"{symbol} 缺少日线数据"
        )

        return summary, warnings

    daily_snapshot = load_json_file(daily_path)
    daily_data = daily_snapshot.get("data", {})
    daily_bars = daily_data.get("bars", [])

    valid_daily_bars = [
        bar
        for bar in daily_bars
        if safe_float(bar.get("close")) is not None
    ]

    valid_daily_bars.sort(
        key=lambda bar: bar.get("timestamp", "")
    )

    closes = [
        safe_float(bar.get("close"))
        for bar in valid_daily_bars
    ]

    closes = [
        close
        for close in closes
        if close is not None
    ]

    if not closes:
        summary["data_status"] = "invalid_daily_data"
        warnings.append(
            f"{symbol} 日线文件中没有有效收盘价"
        )

        return summary, warnings

    latest_daily_bar = valid_daily_bars[-1]

    ma20 = calculate_moving_average(
        closes,
        periods=20,
    )

    ma50 = calculate_moving_average(
        closes,
        periods=50,
    )

    latest_close = closes[-1]

    summary["daily"] = {
        "bar_count": len(valid_daily_bars),
        "latest_bar_at": latest_daily_bar.get(
            "timestamp"
        ),
        "latest_close": round_optional(
            latest_close
        ),
        "return_1d": round_optional(
            calculate_return(closes, periods=1)
        ),
        "return_5d": round_optional(
            calculate_return(closes, periods=5)
        ),
        "return_20d": round_optional(
            calculate_return(closes, periods=20)
        ),
        "ma20": round_optional(ma20),
        "ma50": round_optional(ma50),
        "price_above_ma20": (
            latest_close > ma20
            if ma20 is not None
            else None
        ),
        "price_above_ma50": (
            latest_close > ma50
            if ma50 is not None
            else None
        ),
        "volatility_20d_annualized": (
            round_optional(
                calculate_annualized_volatility(
                    closes,
                    periods=20,
                )
            )
        ),
    }

    if latest_intraday_directory is None:
        summary["intraday"] = {
            "status": "not_available",
            "bar_count": 0,
        }

        return summary, warnings

    intraday_path = (
        latest_intraday_directory
        / f"{symbol}.json"
    )

    summary["raw_files"]["intraday"] = str(
        intraday_path.relative_to(project_root)
    )

    if not intraday_path.exists():
        summary["intraday"] = {
            "status": "missing",
            "bar_count": 0,
        }

        warnings.append(
            f"{symbol} 缺少最近盘中数据"
        )

        return summary, warnings

    intraday_snapshot = load_json_file(
        intraday_path
    )

    intraday_data = intraday_snapshot.get(
        "data",
        {},
    )

    intraday_bars = intraday_data.get("bars", [])

    valid_intraday_bars = [
        bar
        for bar in intraday_bars
        if (
            safe_float(bar.get("open")) is not None
            and safe_float(bar.get("close")) is not None
        )
    ]

    valid_intraday_bars.sort(
        key=lambda bar: bar.get("timestamp", "")
    )

    if not valid_intraday_bars:
        summary["intraday"] = {
            "status": intraday_snapshot.get(
                "status",
                "no_data",
            ),
            "market_date": intraday_data.get(
                "market_date"
            ),
            "bar_count": 0,
        }

        return summary, warnings

    first_bar = valid_intraday_bars[0]
    latest_bar = valid_intraday_bars[-1]

    session_open = safe_float(
        first_bar.get("open")
    )

    latest_price = safe_float(
        latest_bar.get("close")
    )

    return_from_open = None

    if (
        session_open is not None
        and session_open != 0
        and latest_price is not None
    ):
        return_from_open = (
            latest_price / session_open - 1
        )

    summary["intraday"] = {
        "status": "success",
        "market_date": intraday_data.get(
            "market_date"
        ),
        "bar_count": len(valid_intraday_bars),
        "session_open": round_optional(
            session_open
        ),
        "latest_price": round_optional(
            latest_price
        ),
        "return_from_open": round_optional(
            return_from_open
        ),
        "latest_bar_at": latest_bar.get(
            "timestamp"
        ),
        "requested_end": intraday_data.get(
            "requested_end"
        ),
        "delay_minutes": intraday_data.get(
            "delay_minutes"
        ),
    }

    return summary, warnings


def build_decision_snapshot() -> Path:
    """
    构建供 Codex 阅读的完整决策输入快照。
    """
    project_root = get_project_root()
    symbols = load_symbols()

    snapshots_root = (
        project_root
        / "data"
        / "snapshots"
    )

    account_path = snapshots_root / "account.json"
    positions_path = snapshots_root / "positions.json"
    open_orders_path = (
        snapshots_root / "open_orders.json"
    )

    account_snapshot = load_json_file(
        account_path
    )

    positions_snapshot = load_json_file(
        positions_path
    )

    open_orders_snapshot = load_json_file(
        open_orders_path
    )

    latest_intraday_directory = (
        find_latest_intraday_directory(
            project_root
        )
    )

    market_data: dict[str, Any] = {}
    warnings: list[str] = []

    for symbol in symbols:
        symbol_summary, symbol_warnings = (
            build_symbol_summary(
                symbol=symbol,
                project_root=project_root,
                latest_intraday_directory=(
                    latest_intraday_directory
                ),
            )
        )

        market_data[symbol] = symbol_summary
        warnings.extend(symbol_warnings)

    now_utc = datetime.now(timezone.utc)
    now_new_york = now_utc.astimezone(
        NEW_YORK_TIMEZONE
    )

    positions_data = positions_snapshot.get(
        "data",
        {},
    )

    open_orders_data = open_orders_snapshot.get(
        "data",
        {},
    )

    unavailable_symbols = [
        symbol
        for symbol, symbol_data in market_data.items()
        if symbol_data.get("data_status")
        != "available"
    ]

    result = {
        "schema_version": "1.0",
        "generated_at": now_utc.isoformat(),
        "status": (
            "success"
            if not unavailable_symbols
            else "partial"
        ),
        "run_context": {
            "run_mode": determine_run_mode(
                now_new_york
            ),
            "new_york_time": (
                now_new_york.isoformat()
            ),
            "intraday_market_date": (
                latest_intraday_directory.name
                if latest_intraday_directory
                else None
            ),
            "execution_enabled": False,
            "note": (
                "该文件仅供人工运行 Codex 时读取，"
                "当前不会自动下单。"
            ),
        },
        "account": build_account_summary(
            account_snapshot
        ),
        "portfolio": {
            "position_count": positions_data.get(
                "position_count",
                0,
            ),
            "positions": positions_data.get(
                "positions",
                [],
            ),
        },
        "open_orders": {
            "order_count": open_orders_data.get(
                "order_count",
                0,
            ),
            "orders": open_orders_data.get(
                "orders",
                [],
            ),
        },
        "market": market_data,
        "data_quality": {
            "symbol_count": len(symbols),
            "available_symbol_count": (
                len(symbols)
                - len(unavailable_symbols)
            ),
            "unavailable_symbols": (
                unavailable_symbols
            ),
            "warning_count": len(warnings),
            "warnings": warnings,
        },
        "source_files": {
            "account": str(
                account_path.relative_to(project_root)
            ),
            "positions": str(
                positions_path.relative_to(project_root)
            ),
            "open_orders": str(
                open_orders_path.relative_to(
                    project_root
                )
            ),
        },
    }

    output_path = (
        snapshots_root
        / "decision_input.json"
    )

    save_json_atomically(
        output_path,
        result,
    )

    return output_path


def main() -> int:
    """单独运行本文件时生成决策快照。"""
    try:
        output_path = build_decision_snapshot()

        print("Codex 决策输入生成成功")
        print(f"保存位置：{output_path}")

        return 0

    except Exception as error:
        print("Codex 决策输入生成失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())