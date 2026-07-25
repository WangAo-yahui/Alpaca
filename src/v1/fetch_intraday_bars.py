import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from alpaca_client import create_stock_data_client
from config import get_project_root
from runtime_paths import find_latest_stage_workspace
from validate_coarse_candidates import validate_coarse_candidates
from fetch_account import save_json_atomically


SCRIPT_VERSION = "2026-07-22-coarse-selected-60-v2"

NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

MARKET_OPEN_TIME = time(hour=9, minute=30)
MARKET_CLOSE_TIME = time(hour=16, minute=0)

SIP_DELAY_MINUTES = 20
INTRADAY_MINUTES = 5
BATCH_SIZE = 25
COARSE_OUTPUT_MAX_AGE_HOURS = 24

INTRADAY_TIMEFRAME = TimeFrame(
    INTRADAY_MINUTES,
    TimeFrameUnit.Minute,
)


def load_optional_json(
    file_path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """读取可选JSON文件；失败时返回警告而不是终止任务。"""
    if not file_path.exists():
        return None, f"缺少可选文件：{file_path}"

    try:
        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

    except Exception as error:
        return None, (
            f"读取可选文件失败：{file_path}；{error}"
        )

    if not isinstance(payload, dict):
        return None, (
            f"可选JSON顶层不是对象：{file_path}"
        )

    return payload, None


def parse_iso_datetime(
    value: Any,
) -> datetime | None:
    """解析ISO 8601时间，并确保结果带时区。"""
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


def normalize_symbol(
    value: Any,
) -> str:
    """将股票代码标准化为大写字符串。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def append_symbol(
    symbols: list[str],
    seen: set[str],
    reasons: dict[str, list[str]],
    raw_symbol: Any,
    reason: str,
) -> None:
    """按原始顺序加入代码，并记录进入盘中池的原因。"""
    symbol = normalize_symbol(raw_symbol)

    if not symbol:
        return

    reasons.setdefault(symbol, [])

    if reason not in reasons[symbol]:
        reasons[symbol].append(reason)

    if symbol in seen:
        return

    seen.add(symbol)
    symbols.append(symbol)


def extract_symbols_from_records(
    payload: dict[str, Any] | None,
    record_path: tuple[str, ...],
) -> list[str]:
    """从嵌套记录数组中提取symbol字段。"""
    current: Any = payload

    for key in record_path:
        if not isinstance(current, dict):
            return []

        current = current.get(key)

    if not isinstance(current, list):
        return []

    result: list[str] = []

    for record in current:
        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if symbol:
            result.append(symbol)

    return result


def load_validated_coarse_selection(
) -> tuple[
    Path,
    dict[str, Any],
]:
    """读取并再次校验最近一次粗选60只结果。"""
    project_root = get_project_root()

    workspace = find_latest_stage_workspace(
        "coarse_selection",
        project_root=project_root,
    )

    validation = validate_coarse_candidates(
        workspace=workspace,
        max_age_hours=(
            COARSE_OUTPUT_MAX_AGE_HOURS
        ),
    )

    if not validation["valid"]:
        raise RuntimeError(
            "粗选结果未通过Python校验，"
            "不能下载第二阶段盘中行情：\n- "
            + "\n- ".join(
                validation["errors"]
            )
        )

    output_path = (
        workspace
        / "output"
        / "coarse_candidates.json"
    )

    with output_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            "coarse_candidates.json顶层必须是对象"
        )

    return workspace, payload


def collect_intraday_symbols(
) -> tuple[
    list[str],
    dict[str, list[str]],
    list[str],
]:
    """
    决定第二阶段需要下载盘中数据的标的。

    主范围必须来自已通过Python校验的60只粗选结果。
    当前持仓和未完成订单作为安全兜底再次加入，避免旧状态或
    人工修改导致持仓管理标的被遗漏。
    """
    project_root = get_project_root()

    (
        coarse_workspace,
        coarse_output,
    ) = load_validated_coarse_selection()

    symbols: list[str] = []
    seen: set[str] = set()
    reasons: dict[str, list[str]] = {}
    warnings: list[str] = []

    selected = coarse_output.get(
        "selected",
        [],
    )

    if not isinstance(selected, list):
        raise ValueError(
            "粗选结果selected必须是数组"
        )

    for record in selected:
        if not isinstance(record, dict):
            continue

        append_symbol(
            symbols=symbols,
            seen=seen,
            reasons=reasons,
            raw_symbol=record.get("symbol"),
            reason="validated_coarse_selection",
        )

    if len(symbols) != 60:
        raise RuntimeError(
            "通过校验的粗选结果未能提取出"
            f"恰好60只标的：{len(symbols)}"
        )

    positions_path = (
        project_root
        / "data"
        / "snapshots"
        / "positions.json"
    )

    positions_payload, warning = (
        load_optional_json(positions_path)
    )

    if warning:
        warnings.append(warning)

    for symbol in extract_symbols_from_records(
        payload=positions_payload,
        record_path=("data", "positions"),
    ):
        append_symbol(
            symbols=symbols,
            seen=seen,
            reasons=reasons,
            raw_symbol=symbol,
            reason="current_position_safety_include",
        )

    open_orders_path = (
        project_root
        / "data"
        / "snapshots"
        / "open_orders.json"
    )

    open_orders_payload, warning = (
        load_optional_json(open_orders_path)
    )

    if warning:
        warnings.append(warning)

    for symbol in extract_symbols_from_records(
        payload=open_orders_payload,
        record_path=("data", "orders"),
    ):
        append_symbol(
            symbols=symbols,
            seen=seen,
            reasons=reasons,
            raw_symbol=symbol,
            reason="open_order_safety_include",
        )

    warnings.append(
        "盘中下载主范围来自已验证粗选结果："
        f"{coarse_workspace}"
    )

    return symbols, reasons, warnings

def get_market_query_window(
    now_new_york: datetime,
) -> tuple[
    date,
    datetime,
    datetime,
    str,
]:
    """
    计算本轮盘中K线查询窗口。

    返回：
    - 纽约市场日期
    - 查询开始时间
    - 查询结束时间
    - 当前窗口状态
    """
    market_date = now_new_york.date()

    market_open = datetime.combine(
        market_date,
        MARKET_OPEN_TIME,
        tzinfo=NEW_YORK_TIMEZONE,
    )

    market_close = datetime.combine(
        market_date,
        MARKET_CLOSE_TIME,
        tzinfo=NEW_YORK_TIMEZONE,
    )

    delayed_now = (
        now_new_york
        - timedelta(
            minutes=SIP_DELAY_MINUTES
        )
    )

    query_end = min(
        delayed_now,
        market_close,
    )

    if now_new_york.weekday() >= 5:
        return (
            market_date,
            market_open,
            market_open,
            "market_closed_weekend",
        )

    if query_end <= market_open:
        return (
            market_date,
            market_open,
            market_open,
            "before_delayed_data_available",
        )

    if query_end < market_close:
        return (
            market_date,
            market_open,
            query_end,
            "partial_session",
        )

    return (
        market_date,
        market_open,
        market_close,
        "complete_session",
    )


def serialize_number(
    value: Any,
) -> float | int | None:
    """将Alpaca数值转换为JSON可保存的普通数值。"""
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def serialize_bar(
    bar: Any,
) -> dict[str, Any]:
    """将Alpaca Bar对象转换为JSON字典。"""
    timestamp = getattr(
        bar,
        "timestamp",
        None,
    )

    return {
        "timestamp": (
            timestamp.isoformat()
            if isinstance(timestamp, datetime)
            else str(timestamp)
            if timestamp is not None
            else None
        ),
        "open": serialize_number(
            getattr(bar, "open", None)
        ),
        "high": serialize_number(
            getattr(bar, "high", None)
        ),
        "low": serialize_number(
            getattr(bar, "low", None)
        ),
        "close": serialize_number(
            getattr(bar, "close", None)
        ),
        "volume": serialize_number(
            getattr(bar, "volume", None)
        ),
        "trade_count": serialize_number(
            getattr(bar, "trade_count", None)
        ),
        "vwap": serialize_number(
            getattr(bar, "vwap", None)
        ),
    }


def sort_and_deduplicate_bars(
    bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按时间排序并去除重复K线。"""
    lookup: dict[str, dict[str, Any]] = {}

    for bar in bars:
        timestamp = bar.get("timestamp")

        if not isinstance(timestamp, str):
            continue

        lookup[timestamp] = bar

    return [
        lookup[timestamp]
        for timestamp in sorted(lookup)
    ]


def calculate_bar_summary(
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算盘中快照摘要。"""
    if not bars:
        return {
            "bar_count": 0,
            "first_bar_at": None,
            "last_bar_at": None,
            "session_open": None,
            "session_high": None,
            "session_low": None,
            "latest_close": None,
            "total_volume": 0,
        }

    highs = [
        value
        for value in (
            serialize_number(bar.get("high"))
            for bar in bars
        )
        if value is not None
    ]

    lows = [
        value
        for value in (
            serialize_number(bar.get("low"))
            for bar in bars
        )
        if value is not None
    ]

    volumes = [
        value
        for value in (
            serialize_number(bar.get("volume"))
            for bar in bars
        )
        if value is not None
    ]

    return {
        "bar_count": len(bars),
        "first_bar_at": bars[0].get(
            "timestamp"
        ),
        "last_bar_at": bars[-1].get(
            "timestamp"
        ),
        "session_open": bars[0].get("open"),
        "session_high": (
            max(highs)
            if highs
            else None
        ),
        "session_low": (
            min(lows)
            if lows
            else None
        ),
        "latest_close": bars[-1].get(
            "close"
        ),
        "total_volume": sum(volumes),
    }


def build_intraday_snapshot(
    symbol: str,
    bars: list[dict[str, Any]],
    market_date: date,
    query_start: datetime,
    query_end: datetime,
    window_status: str,
    selection_reasons: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """构建单个标的的盘中快照。"""
    normalized_bars = (
        sort_and_deduplicate_bars(bars)
    )

    summary = calculate_bar_summary(
        normalized_bars
    )

    status = (
        "success"
        if normalized_bars
        else "no_data"
    )

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "alpaca_stock_bars",
        "status": status,
        "data": {
            "symbol": symbol,
            "market_date": (
                market_date.isoformat()
            ),
            "market_timezone": (
                "America/New_York"
            ),
            "timeframe": "5Min",
            "feed": "sip",
            "adjustment": "all",
            "sip_delay_minutes": (
                SIP_DELAY_MINUTES
            ),
            "window_status": window_status,
            "query_start": (
                query_start.isoformat()
            ),
            "query_end": (
                query_end.isoformat()
            ),
            "selection_reasons": (
                selection_reasons
            ),
            **summary,
            "bars": normalized_bars,
            "warnings": warnings or [],
        },
    }


def get_response_data(
    response: Any,
) -> dict[str, Any]:
    """从alpaca-py返回对象中取得symbol到bars的映射。"""
    data = getattr(response, "data", None)

    if isinstance(data, dict):
        return data

    if isinstance(response, dict):
        return response

    raise RuntimeError(
        "Alpaca盘中行情返回了未知结构："
        f"{type(response).__name__}"
    )


def fetch_batch(
    symbols: list[str],
    query_start: datetime,
    query_end: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """批量获取一组标的的5分钟K线。"""
    data_client = (
        create_stock_data_client()
    )

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=INTRADAY_TIMEFRAME,
        start=query_start,
        end=query_end,
        adjustment=Adjustment.ALL,
        feed=DataFeed.SIP,
    )

    response = data_client.get_stock_bars(
        request
    )

    response_data = get_response_data(
        response
    )

    result: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for symbol in symbols:
        raw_bars = response_data.get(
            symbol,
            [],
        )

        if raw_bars is None:
            raw_bars = []

        result[symbol] = [
            serialize_bar(bar)
            for bar in raw_bars
        ]

    return result


def fetch_batch_with_fallback(
    symbols: list[str],
    query_start: datetime,
    query_end: datetime,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, str],
]:
    """
    批次失败时递归拆分，避免一个异常代码拖累整批。
    """
    if not symbols:
        return {}, {}

    try:
        return (
            fetch_batch(
                symbols=symbols,
                query_start=query_start,
                query_end=query_end,
            ),
            {},
        )

    except Exception as error:
        if len(symbols) == 1:
            return {}, {
                symbols[0]: str(error)
            }

        middle = len(symbols) // 2

        left_data, left_errors = (
            fetch_batch_with_fallback(
                symbols=symbols[:middle],
                query_start=query_start,
                query_end=query_end,
            )
        )

        right_data, right_errors = (
            fetch_batch_with_fallback(
                symbols=symbols[middle:],
                query_start=query_start,
                query_end=query_end,
            )
        )

        return (
            {
                **left_data,
                **right_data,
            },
            {
                **left_errors,
                **right_errors,
            },
        )


def chunk_symbols(
    symbols: list[str],
    batch_size: int,
) -> list[list[str]]:
    """将标的列表拆分成固定大小批次。"""
    return [
        symbols[index:index + batch_size]
        for index in range(
            0,
            len(symbols),
            batch_size,
        )
    ]


def save_intraday_snapshot(
    market_date: date,
    symbol: str,
    snapshot: dict[str, Any],
) -> Path:
    """保存单个标的的盘中快照。"""
    output_path = (
        get_project_root()
        / "data"
        / "bars"
        / "intraday"
        / market_date.isoformat()
        / f"{symbol}.json"
    )

    save_json_atomically(
        output_path,
        snapshot,
    )

    return output_path


def fetch_and_save_intraday_bars(
) -> tuple[
    list[str],
    list[str],
    list[str],
]:
    """
    获取本轮需要关注标的的5分钟K线。

    返回：
    - 有盘中数据的标的
    - 当前无盘中数据的标的
    - 下载失败的标的
    """
    (
        symbols,
        selection_reasons,
        selection_warnings,
    ) = collect_intraday_symbols()

    if not symbols:
        raise ValueError(
            "本轮盘中下载范围为空"
        )

    now_new_york = datetime.now(
        NEW_YORK_TIMEZONE
    )

    (
        market_date,
        query_start,
        query_end,
        window_status,
    ) = get_market_query_window(
        now_new_york
    )

    successful_symbols: list[str] = []
    no_data_symbols: list[str] = []
    failed_symbols: list[str] = []
    all_errors: dict[str, str] = {}

    if query_end <= query_start:
        for symbol in symbols:
            snapshot = build_intraday_snapshot(
                symbol=symbol,
                bars=[],
                market_date=market_date,
                query_start=query_start,
                query_end=query_end,
                window_status=window_status,
                selection_reasons=(
                    selection_reasons.get(
                        symbol,
                        [],
                    )
                ),
                warnings=selection_warnings,
            )

            save_intraday_snapshot(
                market_date=market_date,
                symbol=symbol,
                snapshot=snapshot,
            )

            no_data_symbols.append(symbol)

        print(
            "本轮盘中下载范围："
            f"{len(symbols)} 个标的"
        )
        print(
            "当前窗口状态："
            f"{window_status}"
        )

        if selection_warnings:
            print("盘中范围警告：")

            for warning in selection_warnings:
                print(f"- {warning}")

        return (
            successful_symbols,
            no_data_symbols,
            failed_symbols,
        )

    batches = chunk_symbols(
        symbols=symbols,
        batch_size=BATCH_SIZE,
    )

    for batch_index, batch in enumerate(
        batches,
        start=1,
    ):
        print(
            f"盘中批次 {batch_index}/"
            f"{len(batches)}："
            f"{len(batch)} 个标的"
        )

        batch_data, batch_errors = (
            fetch_batch_with_fallback(
                symbols=batch,
                query_start=query_start,
                query_end=query_end,
            )
        )

        all_errors.update(batch_errors)

        for symbol in batch:
            if symbol in batch_errors:
                failed_symbols.append(symbol)
                continue

            bars = batch_data.get(
                symbol,
                [],
            )

            snapshot = build_intraday_snapshot(
                symbol=symbol,
                bars=bars,
                market_date=market_date,
                query_start=query_start,
                query_end=query_end,
                window_status=window_status,
                selection_reasons=(
                    selection_reasons.get(
                        symbol,
                        [],
                    )
                ),
                warnings=selection_warnings,
            )

            save_intraday_snapshot(
                market_date=market_date,
                symbol=symbol,
                snapshot=snapshot,
            )

            if bars:
                successful_symbols.append(symbol)
            else:
                no_data_symbols.append(symbol)

    if failed_symbols:
        print("盘中下载失败详情：")

        for symbol in failed_symbols:
            print(
                f"- {symbol}："
                f"{all_errors.get(symbol, '未知错误')}"
            )

    print(
        "本轮盘中下载范围："
        f"{len(symbols)} 个标的"
    )
    print(
        "当前窗口状态："
        f"{window_status}"
    )

    if selection_warnings:
        print("盘中范围警告：")

        for warning in selection_warnings:
            print(f"- {warning}")

    return (
        successful_symbols,
        no_data_symbols,
        failed_symbols,
    )


def main() -> int:
    """单独运行本文件时下载盘中5分钟K线。"""
    print(f"脚本版本：{SCRIPT_VERSION}")

    try:
        (
            successful_symbols,
            no_data_symbols,
            failed_symbols,
        ) = fetch_and_save_intraday_bars()

        print()
        print(
            "盘中成功数量："
            f"{len(successful_symbols)}"
        )
        print(
            "盘中暂无数据数量："
            f"{len(no_data_symbols)}"
        )
        print(
            "盘中失败数量："
            f"{len(failed_symbols)}"
        )

        if no_data_symbols:
            print(
                "当前暂无盘中数据："
                + ", ".join(no_data_symbols)
            )

        if failed_symbols:
            print(
                "盘中失败标的："
                + ", ".join(failed_symbols)
            )
            return 1

        return 0

    except Exception as error:
        print("盘中K线下载任务失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())