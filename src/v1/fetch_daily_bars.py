import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from alpaca_client import create_stock_data_client
from config import get_project_root, load_symbols
from fetch_account import enum_value, save_json_atomically


NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

TIMEFRAME = TimeFrame.Day
ADJUSTMENT = Adjustment.ALL
DATA_FEED = DataFeed.SIP

TARGET_HISTORY_BARS = 300
INITIAL_LOOKBACK_CALENDAR_DAYS = 500
INCREMENTAL_OVERLAP_CALENDAR_DAYS = 14
FULL_REFRESH_INTERVAL_CALENDAR_DAYS = 7
SIP_DELAY_MINUTES = 20

# Historical bars 的 limit 是所有代码合计，而不是每个代码分别计算。
# 25 个代码 × 约 350 根日线通常低于每页 10,000 根的上限。
BATCH_SIZE = 25
REQUEST_LIMIT = 10_000


class DailyBarsError(RuntimeError):
    """日线下载或本地合并失败。"""


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


def safe_int(value: Any) -> int | None:
    """将数值安全转换为整数。"""
    if value is None:
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number


def parse_iso_datetime(value: Any) -> datetime | None:
    """解析 ISO 8601 时间并确保具有时区。"""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def chunk_symbols(
    symbols: list[str],
    batch_size: int,
) -> list[list[str]]:
    """将代码列表拆成固定大小的批次。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于0")

    return [
        symbols[index:index + batch_size]
        for index in range(0, len(symbols), batch_size)
    ]


def get_daily_file_path(
    project_root: Path,
    symbol: str,
) -> Path:
    """返回单个标的的日线快照路径。"""
    return (
        project_root
        / "data"
        / "bars"
        / "daily"
        / f"{symbol}.json"
    )


def load_existing_snapshot(
    file_path: Path,
) -> dict[str, Any] | None:
    """读取已有日线快照；文件不存在时返回 None。"""
    if not file_path.exists():
        return None

    try:
        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            snapshot = json.load(file)
    except Exception as error:
        raise DailyBarsError(
            f"无法读取已有日线文件 {file_path}：{error}"
        ) from error

    if not isinstance(snapshot, dict):
        raise DailyBarsError(
            f"已有日线文件顶层不是对象：{file_path}"
        )

    return snapshot


def extract_existing_bars(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """从旧快照中提取并排序日线数组。"""
    if snapshot is None:
        return []

    bars = (
        snapshot
        .get("data", {})
        .get("bars", [])
    )

    if not isinstance(bars, list):
        raise DailyBarsError(
            "已有日线快照中的 data.bars 不是数组"
        )

    valid_bars = [
        bar
        for bar in bars
        if isinstance(bar, dict)
        and isinstance(bar.get("timestamp"), str)
    ]

    valid_bars.sort(
        key=lambda bar: bar["timestamp"]
    )

    return valid_bars


def get_last_bar_time(
    bars: list[dict[str, Any]],
) -> datetime | None:
    """读取已有日线中的最后一个有效时间。"""
    for bar in reversed(bars):
        timestamp = parse_iso_datetime(
            bar.get("timestamp")
        )

        if timestamp is not None:
            return timestamp

    return None


def should_full_refresh(
    snapshot: dict[str, Any] | None,
    existing_bars: list[dict[str, Any]],
    now_utc: datetime,
) -> bool:
    """
    判断是否需要重新下载完整历史。

    使用 Adjustment.ALL 时，公司行为可能改变旧日线，
    因此即使本地已有数据，也需要定期完整刷新。
    """
    if snapshot is None:
        return True

    if len(existing_bars) < TARGET_HISTORY_BARS:
        return True

    data = snapshot.get("data", {})

    if not isinstance(data, dict):
        return True

    if data.get("adjustment") != enum_value(ADJUSTMENT):
        return True

    if data.get("feed") != enum_value(DATA_FEED):
        return True

    last_full_refresh = parse_iso_datetime(
        data.get("last_full_refresh_at")
    )

    if last_full_refresh is None:
        return True

    age_days = (
        now_utc
        - last_full_refresh.astimezone(timezone.utc)
    ).total_seconds() / 86_400

    return age_days >= FULL_REFRESH_INTERVAL_CALENDAR_DAYS


def serialize_bar(bar: Any) -> dict[str, Any] | None:
    """将 alpaca-py Bar 对象转换为稳定 JSON 结构。"""
    timestamp = parse_iso_datetime(
        getattr(bar, "timestamp", None)
    )

    open_price = safe_float(
        getattr(bar, "open", None)
    )
    high_price = safe_float(
        getattr(bar, "high", None)
    )
    low_price = safe_float(
        getattr(bar, "low", None)
    )
    close_price = safe_float(
        getattr(bar, "close", None)
    )
    volume = safe_float(
        getattr(bar, "volume", None)
    )

    if (
        timestamp is None
        or open_price is None
        or high_price is None
        or low_price is None
        or close_price is None
        or volume is None
    ):
        return None

    return {
        "timestamp": timestamp.isoformat(),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "trade_count": safe_int(
            getattr(bar, "trade_count", None)
        ),
        "vwap": safe_float(
            getattr(bar, "vwap", None)
        ),
    }


def extract_response_mapping(
    response: Any,
) -> dict[str, list[Any]]:
    """兼容 BarSet 和 raw dict 两种响应形式。"""
    response_data = getattr(response, "data", None)

    if isinstance(response_data, dict):
        return {
            str(symbol).strip().upper(): bars
            for symbol, bars in response_data.items()
            if isinstance(bars, list)
        }

    if isinstance(response, dict):
        raw_bars = response.get("bars", response)

        if isinstance(raw_bars, dict):
            return {
                str(symbol).strip().upper(): bars
                for symbol, bars in raw_bars.items()
                if isinstance(bars, list)
            }

    raise DailyBarsError(
        "Alpaca日线接口返回了无法识别的数据结构"
    )


def fetch_batch_once(
    client: Any,
    symbols: list[str],
    start_time: datetime,
    end_time: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """批量请求一组标的的日线。"""
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TIMEFRAME,
        start=start_time,
        end=end_time,
        limit=REQUEST_LIMIT,
        adjustment=ADJUSTMENT,
        feed=DATA_FEED,
    )

    response = client.get_stock_bars(request)
    raw_mapping = extract_response_mapping(response)

    result: dict[str, list[dict[str, Any]]] = {}

    for symbol in symbols:
        serialized_bars: list[dict[str, Any]] = []

        for bar in raw_mapping.get(symbol, []):
            serialized = serialize_bar(bar)

            if serialized is not None:
                serialized_bars.append(serialized)

        serialized_bars.sort(
            key=lambda item: item["timestamp"]
        )

        result[symbol] = serialized_bars

    return result


def fetch_batch_with_fallback(
    client: Any,
    symbols: list[str],
    start_time: datetime,
    end_time: datetime,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, str],
]:
    """
    批量请求失败时递归拆分批次。

    这样单个异常代码不会拖累整个批次。
    """
    if not symbols:
        return {}, {}

    try:
        return (
            fetch_batch_once(
                client=client,
                symbols=symbols,
                start_time=start_time,
                end_time=end_time,
            ),
            {},
        )

    except Exception as error:
        if len(symbols) == 1:
            return {}, {symbols[0]: str(error)}

        midpoint = len(symbols) // 2

        left_data, left_errors = (
            fetch_batch_with_fallback(
                client=client,
                symbols=symbols[:midpoint],
                start_time=start_time,
                end_time=end_time,
            )
        )

        right_data, right_errors = (
            fetch_batch_with_fallback(
                client=client,
                symbols=symbols[midpoint:],
                start_time=start_time,
                end_time=end_time,
            )
        )

        left_data.update(right_data)
        left_errors.update(right_errors)

        return left_data, left_errors


def should_exclude_current_session_bar(
    now_ny: datetime,
) -> bool:
    """SIP 延迟窗口结束前，不保存尚未完整结束的当日日线。"""
    if now_ny.weekday() >= 5:
        return False

    completed_cutoff = time(
        hour=16,
        minute=SIP_DELAY_MINUTES,
    )

    return now_ny.time() < completed_cutoff


def filter_incomplete_daily_bar(
    bars: list[dict[str, Any]],
    now_ny: datetime,
) -> list[dict[str, Any]]:
    """交易日收盘并度过 SIP 延迟前，移除当日未完成日线。"""
    if not should_exclude_current_session_bar(now_ny):
        return bars

    current_market_date = now_ny.date()
    filtered: list[dict[str, Any]] = []

    for bar in bars:
        timestamp = parse_iso_datetime(
            bar.get("timestamp")
        )

        if timestamp is None:
            continue

        bar_market_date = timestamp.astimezone(
            NEW_YORK_TIMEZONE
        ).date()

        if bar_market_date == current_market_date:
            continue

        filtered.append(bar)

    return filtered


def merge_bars(
    existing_bars: list[dict[str, Any]],
    fetched_bars: list[dict[str, Any]],
    now_ny: datetime,
) -> list[dict[str, Any]]:
    """按时间戳合并、去重，并保留最近300根完整日线。"""
    merged_by_timestamp: dict[
        str,
        dict[str, Any],
    ] = {}

    for bar in existing_bars:
        timestamp = bar.get("timestamp")

        if isinstance(timestamp, str):
            merged_by_timestamp[timestamp] = bar

    # 新下载数据覆盖同一时间戳的旧数据。
    for bar in fetched_bars:
        timestamp = bar.get("timestamp")

        if isinstance(timestamp, str):
            merged_by_timestamp[timestamp] = bar

    merged = sorted(
        merged_by_timestamp.values(),
        key=lambda item: item["timestamp"],
    )

    merged = filter_incomplete_daily_bar(
        bars=merged,
        now_ny=now_ny,
    )

    if len(merged) > TARGET_HISTORY_BARS:
        merged = merged[-TARGET_HISTORY_BARS:]

    return merged


def build_snapshot_payload(
    symbol: str,
    bars: list[dict[str, Any]],
    generated_at: datetime,
    requested_start: datetime,
    requested_end: datetime,
    update_mode: str,
    previous_bar_count: int,
    fetched_bar_count: int,
    last_full_refresh_at: str | None,
) -> dict[str, Any]:
    """构建与现有下游程序兼容的日线快照。"""
    first_bar_at = (
        bars[0]["timestamp"]
        if bars
        else None
    )

    last_bar_at = (
        bars[-1]["timestamp"]
        if bars
        else None
    )

    return {
        "generated_at": generated_at.isoformat(),
        "source": "alpaca_stock_historical_data",
        "status": "success",
        "data": {
            "symbol": symbol,
            "timeframe": "1Day",
            "adjustment": enum_value(ADJUSTMENT),
            "feed": enum_value(DATA_FEED),
            "update_mode": update_mode,
            "requested_start": (
                requested_start.isoformat()
            ),
            "requested_end": (
                requested_end.isoformat()
            ),
            "target_history_bars": (
                TARGET_HISTORY_BARS
            ),
            "previous_bar_count": (
                previous_bar_count
            ),
            "fetched_bar_count": (
                fetched_bar_count
            ),
            "bar_count": len(bars),
            "first_bar_at": first_bar_at,
            "last_bar_at": last_bar_at,
            "last_full_refresh_at": (
                last_full_refresh_at
            ),
            "bars": bars,
        },
    }


def fetch_and_save_daily_bars() -> tuple[
    list[str],
    list[str],
]:
    """
    批量更新 universe 中所有标的的日线。

    返回：
    - 成功代码列表
    - 失败代码列表
    """
    project_root = get_project_root()
    symbols = load_symbols()

    if not symbols:
        raise DailyBarsError(
            "universe 中没有可下载的标的"
        )

    generated_at = datetime.now(timezone.utc)
    now_ny = generated_at.astimezone(
        NEW_YORK_TIMEZONE
    )

    # 避免无实时 SIP 权限时请求到不可访问的最近窗口。
    request_end = (
        generated_at
        - timedelta(minutes=SIP_DELAY_MINUTES)
    )

    existing_snapshots: dict[
        str,
        dict[str, Any] | None,
    ] = {}
    existing_bars_by_symbol: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    full_refresh_symbols: list[str] = []
    incremental_symbols: list[str] = []
    failed_reasons: dict[str, str] = {}

    for symbol in symbols:
        file_path = get_daily_file_path(
            project_root=project_root,
            symbol=symbol,
        )

        try:
            snapshot = load_existing_snapshot(
                file_path
            )
            existing_bars = extract_existing_bars(
                snapshot
            )
        except Exception as error:
            failed_reasons[symbol] = str(error)
            continue

        existing_snapshots[symbol] = snapshot
        existing_bars_by_symbol[symbol] = (
            existing_bars
        )

        if should_full_refresh(
            snapshot=snapshot,
            existing_bars=existing_bars,
            now_utc=generated_at,
        ):
            full_refresh_symbols.append(symbol)
        else:
            incremental_symbols.append(symbol)

    client = create_stock_data_client()

    fetched_by_symbol: dict[
        str,
        list[dict[str, Any]],
    ] = {}
    request_start_by_symbol: dict[
        str,
        datetime,
    ] = {}
    update_mode_by_symbol: dict[str, str] = {}

    initial_start = (
        request_end
        - timedelta(
            days=INITIAL_LOOKBACK_CALENDAR_DAYS
        )
    )

    for batch_number, batch in enumerate(
        chunk_symbols(
            full_refresh_symbols,
            BATCH_SIZE,
        ),
        start=1,
    ):
        print(
            "完整日线批次 "
            f"{batch_number}："
            f"{len(batch)} 个标的"
        )

        batch_data, batch_errors = (
            fetch_batch_with_fallback(
                client=client,
                symbols=batch,
                start_time=initial_start,
                end_time=request_end,
            )
        )

        fetched_by_symbol.update(batch_data)
        failed_reasons.update(batch_errors)

        for symbol in batch:
            request_start_by_symbol[symbol] = (
                initial_start
            )
            update_mode_by_symbol[symbol] = (
                "full_refresh"
            )

    # 增量标的按批次内最早的已有时间统一请求。
    for batch_number, batch in enumerate(
        chunk_symbols(
            incremental_symbols,
            BATCH_SIZE,
        ),
        start=1,
    ):
        latest_times = [
            get_last_bar_time(
                existing_bars_by_symbol[symbol]
            )
            for symbol in batch
        ]

        valid_latest_times = [
            item
            for item in latest_times
            if item is not None
        ]

        if not valid_latest_times:
            batch_start = initial_start
            batch_mode = "full_refresh"
        else:
            earliest_latest = min(
                valid_latest_times
            )
            batch_start = (
                earliest_latest
                - timedelta(
                    days=(
                        INCREMENTAL_OVERLAP_CALENDAR_DAYS
                    )
                )
            )
            batch_mode = "incremental"

        print(
            "增量日线批次 "
            f"{batch_number}："
            f"{len(batch)} 个标的"
        )

        batch_data, batch_errors = (
            fetch_batch_with_fallback(
                client=client,
                symbols=batch,
                start_time=batch_start,
                end_time=request_end,
            )
        )

        fetched_by_symbol.update(batch_data)
        failed_reasons.update(batch_errors)

        for symbol in batch:
            request_start_by_symbol[symbol] = (
                batch_start
            )
            update_mode_by_symbol[symbol] = (
                batch_mode
            )

    successful_symbols: list[str] = []

    for symbol in symbols:
        if symbol in failed_reasons:
            continue

        existing_bars = existing_bars_by_symbol.get(
            symbol,
            [],
        )
        fetched_bars = fetched_by_symbol.get(
            symbol,
            [],
        )

        update_mode = update_mode_by_symbol.get(
            symbol,
            "full_refresh",
        )

        # 完整刷新应完全采用新数据，避免保留已被公司行为修订的旧值。
        merge_base = (
            []
            if update_mode == "full_refresh"
            else existing_bars
        )

        merged_bars = merge_bars(
            existing_bars=merge_base,
            fetched_bars=fetched_bars,
            now_ny=now_ny,
        )

        if not merged_bars:
            failed_reasons[symbol] = (
                "没有获得任何可保存的完整日线"
            )
            continue

        previous_snapshot = (
            existing_snapshots.get(symbol)
        )
        previous_data = (
            previous_snapshot.get("data", {})
            if isinstance(previous_snapshot, dict)
            else {}
        )

        if update_mode == "full_refresh":
            last_full_refresh_at = (
                generated_at.isoformat()
            )
        else:
            preserved_value = (
                previous_data.get(
                    "last_full_refresh_at"
                )
                if isinstance(previous_data, dict)
                else None
            )

            last_full_refresh_at = (
                preserved_value
                if isinstance(
                    preserved_value,
                    str,
                )
                else None
            )

        payload = build_snapshot_payload(
            symbol=symbol,
            bars=merged_bars,
            generated_at=generated_at,
            requested_start=(
                request_start_by_symbol.get(
                    symbol,
                    initial_start,
                )
            ),
            requested_end=request_end,
            update_mode=update_mode,
            previous_bar_count=len(existing_bars),
            fetched_bar_count=len(fetched_bars),
            last_full_refresh_at=(
                last_full_refresh_at
            ),
        )

        output_path = get_daily_file_path(
            project_root=project_root,
            symbol=symbol,
        )

        try:
            save_json_atomically(
                output_path,
                payload,
            )
        except Exception as error:
            failed_reasons[symbol] = (
                f"保存日线失败：{error}"
            )
            continue

        successful_symbols.append(symbol)

    failed_symbols = [
        symbol
        for symbol in symbols
        if symbol not in successful_symbols
    ]

    if failed_reasons:
        print()
        print("日线失败详情：")

        for symbol in failed_symbols:
            reason = failed_reasons.get(
                symbol,
                "未知错误",
            )
            print(f"- {symbol}: {reason}")

    return successful_symbols, failed_symbols


def main() -> int:
    """单独运行时下载并保存全部日线。"""
    try:
        successful_symbols, failed_symbols = (
            fetch_and_save_daily_bars()
        )

        print()
        print("日线下载完成")
        print(
            "成功数量："
            f"{len(successful_symbols)}"
        )
        print(
            "失败数量："
            f"{len(failed_symbols)}"
        )

        if successful_symbols:
            print(
                "成功标的："
                + ", ".join(successful_symbols)
            )

        if failed_symbols:
            print(
                "失败标的："
                + ", ".join(failed_symbols)
            )
            return 1

        return 0

    except Exception as error:
        print("日线下载失败")
        print(f"错误信息：{error}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())