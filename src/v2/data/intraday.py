"""读取并规范化执行时段所需的分钟线、成交与市场阶段。

作用：提供短窗口行情摘要并按纽约时区识别 regular、extended、overnight 与 closed。
重要性：市场阶段决定订单类型和扩展时段能力，识别不明时必须保守阻止。
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import GetCalendarRequest

from v2.data._normalization import (
    finite_float,
    iso_timestamp,
    normalized_symbol,
    read_field,
    utc_now,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import TemporaryDataError


NEW_YORK_TZ = ZoneInfo("America/New_York")


def determine_market_phase(
    value: datetime | None = None,
    *,
    is_market_holiday: bool = False,
) -> str:
    current = value or datetime.now(NEW_YORK_TZ)
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TZ
        )
    current = current.astimezone(NEW_YORK_TZ)

    if current.weekday() >= 5:
        return "market_closed_weekend"
    if is_market_holiday:
        return "market_closed_holiday"

    clock = current.timetz().replace(
        tzinfo=None
    )
    if time(4, 0) <= clock < time(9, 30):
        return "before_market_open"
    if time(9, 30) <= clock < time(16, 0):
        return "regular_session"
    if time(16, 0) <= clock < time(20, 0):
        return "after_market_close"
    if clock >= time(20, 0) or clock < time(4, 0):
        return "overnight_session"
    return "unknown"


def fetch_market_holiday_status(
    clients: AlpacaClients,
    value: datetime,
) -> bool:
    current = value
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TZ
        )
    market_date = current.astimezone(
        NEW_YORK_TZ
    ).date()
    if market_date.weekday() >= 5:
        return False
    calendar = call_api(
        "get_market_calendar",
        clients.trading.get_calendar,
        GetCalendarRequest(
            start=market_date,
            end=market_date,
        ),
    )
    try:
        sessions = list(calendar)
    except TypeError as error:
        raise TemporaryDataError(
            "Alpaca市场日历响应无效",
            code="MARKET_CALENDAR_INVALID",
            details={
                "exception_type": (
                    error.__class__.__name__
                )
            },
        ) from None
    return len(sessions) == 0


def normalize_minute_bar(
    bar: object,
) -> dict[str, Any] | None:
    timestamp = iso_timestamp(
        read_field(bar, "timestamp")
    )
    values = {
        field: finite_float(
            read_field(bar, field)
        )
        for field in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
    }
    if (
        timestamp is None
        or any(
            value is None
            for value in values.values()
        )
    ):
        return None
    open_price = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    volume = values["volume"]
    assert open_price is not None
    assert high is not None
    assert low is not None
    assert close is not None
    assert volume is not None
    if (
        min(open_price, high, low, close) <= 0
        or volume < 0
        or high < max(open_price, low, close)
        or low > min(open_price, high, close)
    ):
        return None
    return {
        "timestamp": timestamp,
        **values,
        "trade_count": finite_float(
            read_field(bar, "trade_count")
        ),
        "vwap": finite_float(
            read_field(bar, "vwap")
        ),
    }


def _return_percent(
    current: float,
    prior: float,
) -> float | None:
    if prior <= 0:
        return None
    return (current / prior - 1) * 100


def summarize_intraday(
    symbol: str,
    bars: list[object],
    *,
    market_phase: str,
    requested_window: int = 60,
) -> dict[str, Any]:
    normalized_bars = [
        normalized
        for bar in bars
        if (
            normalized := normalize_minute_bar(
                bar
            )
        )
        is not None
    ]
    normalized_bars.sort(
        key=lambda item: item["timestamp"]
    )
    if not normalized_bars:
        return {
            "symbol": normalized_symbol(symbol),
            "status": "no_data",
            "market_phase": market_phase,
            "window_status": "no_data",
            "bar_count": 0,
            "bars": [],
            "summary": None,
        }

    closes = [
        float(bar["close"])
        for bar in normalized_bars
    ]
    volumes = [
        float(bar["volume"])
        for bar in normalized_bars
    ]
    first = normalized_bars[0]
    latest = normalized_bars[-1]
    changes: dict[str, float | None] = {}
    for minutes in (5, 15, 30, 60):
        changes[f"{minutes}m"] = (
            _return_percent(
                closes[-1],
                closes[-(minutes + 1)],
            )
            if len(closes) > minutes
            else None
        )

    returns = [
        (closes[index] / closes[index - 1] - 1)
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    realized_volatility = (
        statistics.pstdev(returns)
        * math.sqrt(len(returns))
        if len(returns) >= 2
        else None
    )
    baseline_volume = (
        statistics.fmean(volumes[:-1])
        if len(volumes) > 1
        else None
    )
    volume_ratio = (
        volumes[-1] / baseline_volume
        if baseline_volume
        and baseline_volume > 0
        else None
    )

    return {
        "symbol": normalized_symbol(symbol),
        "status": "success",
        "market_phase": market_phase,
        "window_status": (
            "complete"
            if len(normalized_bars)
            >= requested_window
            else "partial"
        ),
        "bar_count": len(normalized_bars),
        "bars": normalized_bars,
        "summary": {
            "session_open": first["open"],
            "session_high": max(
                float(bar["high"])
                for bar in normalized_bars
            ),
            "session_low": min(
                float(bar["low"])
                for bar in normalized_bars
            ),
            "latest_close": latest["close"],
            "session_volume": sum(volumes),
            "change_from_open_percent": (
                _return_percent(
                    float(latest["close"]),
                    float(first["open"]),
                )
            ),
            "window_changes_percent": changes,
            "realized_volatility": (
                realized_volatility
            ),
            "latest_volume_ratio": volume_ratio,
        },
    }


def _bar_mapping(
    response: object,
) -> dict[str, list[object]]:
    data = (
        response
        if isinstance(response, dict)
        else read_field(response, "data", {})
    )
    if not isinstance(data, dict):
        return {}
    return {
        normalized_symbol(symbol): list(bars)
        for symbol, bars in data.items()
    }


def fetch_intraday_summaries(
    clients: AlpacaClients,
    symbols: list[str],
    *,
    start: datetime,
    end: datetime | None = None,
    window: int = 60,
    market_phase: str | None = None,
) -> dict[str, dict[str, Any]]:
    unique = sorted(
        {
            normalized_symbol(symbol)
            for symbol in symbols
            if normalized_symbol(symbol)
        }
    )
    if not unique:
        return {}
    response = call_api(
        "get_stock_intraday_bars",
        clients.stock_data.get_stock_bars,
        StockBarsRequest(
            symbol_or_symbols=unique,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end or utc_now(),
            limit=max(window * len(unique), window),
        ),
    )
    mapping = _bar_mapping(response)
    phase = market_phase or determine_market_phase(
        end
    )
    return {
        symbol: summarize_intraday(
            symbol,
            mapping.get(symbol, []),
            market_phase=phase,
            requested_window=window,
        )
        for symbol in unique
    }
