"""管理账户无关的共享日线行情及增量刷新。

作用：规范化、合并、校验并持久化股票日线，同时只读兼容旧 Stage C 数据目录。
重要性：粗选签名和质量门控依赖这里的数据，损坏或过期行情不能被当作可执行事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from v2.config import V2Config
from v2.data._normalization import (
    as_utc_datetime,
    finite_float,
    finite_int,
    iso_timestamp,
    normalized_symbol,
    read_field,
    utc_now,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import V2Error
from v2.runtime import (
    atomic_write_json,
    build_shared_data_paths,
    load_json_object,
)


ADJUSTMENTS = {
    "all": Adjustment.ALL,
    "raw": Adjustment.RAW,
    "split": Adjustment.SPLIT,
    "dividend": Adjustment.DIVIDEND,
}
DATA_FEEDS = {
    "sip": DataFeed.SIP,
    "iex": DataFeed.IEX,
}


def normalize_daily_bar(
    bar: object,
) -> dict[str, Any] | None:
    timestamp = iso_timestamp(
        read_field(bar, "timestamp")
    )
    prices = {
        field: finite_float(
            read_field(bar, field)
        )
        for field in (
            "open",
            "high",
            "low",
            "close",
        )
    }
    volume = finite_float(
        read_field(bar, "volume")
    )
    if (
        timestamp is None
        or volume is None
        or any(
            value is None
            for value in prices.values()
        )
    ):
        return None

    open_price = prices["open"]
    high = prices["high"]
    low = prices["low"]
    close = prices["close"]
    assert open_price is not None
    assert high is not None
    assert low is not None
    assert close is not None
    if (
        min(open_price, high, low, close) <= 0
        or volume < 0
        or high < max(open_price, low, close)
        or low > min(open_price, high, close)
    ):
        return None

    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "trade_count": finite_int(
            read_field(bar, "trade_count")
        ),
        "vwap": finite_float(
            read_field(bar, "vwap")
        ),
    }


def merge_daily_bars(
    existing: list[object],
    incoming: list[object],
    *,
    retain: int,
) -> tuple[list[dict[str, Any]], int]:
    by_timestamp: dict[
        str,
        dict[str, Any],
    ] = {}
    invalid = 0
    for raw in [*existing, *incoming]:
        bar = normalize_daily_bar(raw)
        if bar is None:
            invalid += 1
            continue
        by_timestamp[bar["timestamp"]] = bar

    merged = sorted(
        by_timestamp.values(),
        key=lambda item: item["timestamp"],
    )
    return merged[-retain:], invalid


@dataclass(frozen=True)
class DailyBarStore:
    root: Path
    fallback_root: Path | None = None

    @classmethod
    def for_project(
        cls,
        project_root: Path,
    ) -> "DailyBarStore":
        return cls(
            root=build_shared_data_paths(
                project_root=project_root
            ).daily,
            fallback_root=(
                project_root
                / "data"
                / "bars"
                / "daily"
            )
        )

    def path_for(self, symbol: str) -> Path:
        normalized = normalized_symbol(symbol)
        if not normalized or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in normalized
        ):
            raise ValueError(
                f"非法symbol：{symbol}"
            )
        return self.root / f"{normalized}.json"

    def load(
        self,
        symbol: str,
    ) -> dict[str, Any] | None:
        path = self.path_for(symbol)
        if path.exists():
            return load_json_object(path)
        if self.fallback_root is None:
            return None
        fallback = (
            self.fallback_root / path.name
        )
        if not fallback.exists():
            return None
        return load_json_object(fallback)

    def bars(
        self,
        symbol: str,
    ) -> list[object]:
        payload = self.load(symbol)
        if payload is None:
            return []
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return []
        bars = data.get("bars", [])
        return bars if isinstance(bars, list) else []

    def save(
        self,
        symbol: str,
        payload: dict[str, Any],
    ) -> Path:
        path = self.path_for(symbol)
        atomic_write_json(path, payload)
        return path


def _response_bars(
    response: object,
    symbol: str,
) -> list[object]:
    data = (
        response
        if isinstance(response, dict)
        else read_field(response, "data", {})
    )
    if not isinstance(data, dict):
        return []
    bars = (
        data.get(symbol)
        or data.get(symbol.upper())
        or []
    )
    return list(bars)


def _snapshot_payload(
    *,
    symbol: str,
    status: str,
    bars: list[dict[str, Any]],
    previous_count: int,
    fetched_count: int,
    invalid_count: int,
    minimum_bars: int,
    generated_at: datetime,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    if invalid_count:
        warnings.append(
            {
                "code": "INVALID_DAILY_BARS_IGNORED",
                "count": invalid_count,
            }
        )
    if bars and len(bars) < minimum_bars:
        warnings.append(
            {
                "code": "INSUFFICIENT_DAILY_HISTORY",
                "bar_count": len(bars),
                "minimum_bars": minimum_bars,
            }
        )
    return {
        "schema_version": "1.0",
        "generated_at": iso_timestamp(
            generated_at
        ),
        "source": "alpaca_stock_historical_data",
        "status": status,
        "data": {
            "symbol": symbol,
            "timeframe": "1Day",
            "update_mode": "incremental",
            "minimum_history_bars": minimum_bars,
            "previous_bar_count": previous_count,
            "fetched_bar_count": fetched_count,
            "bar_count": len(bars),
            "history_sufficient": (
                len(bars) >= minimum_bars
            ),
            "first_bar_at": (
                bars[0]["timestamp"]
                if bars
                else None
            ),
            "last_bar_at": (
                bars[-1]["timestamp"]
                if bars
                else None
            ),
            "bars": bars,
        },
        "warnings": warnings,
        "errors": [error] if error else [],
    }


def update_daily_bars(
    clients: AlpacaClients,
    symbols: list[str],
    *,
    config: V2Config,
    store: DailyBarStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=utc_now().tzinfo
        )
    market_config = config.market_data
    minimum = int(
        market_config["minimum_daily_bars"]
    )
    retain = int(
        market_config["retained_daily_bars"]
    )
    lookback = int(
        market_config[
            "initial_daily_lookback_days"
        ]
    )
    overlap = int(
        market_config[
            "daily_bar_incremental_overlap_sessions"
        ]
    )
    bar_store = store or DailyBarStore.for_project(
        config.project_root
    )
    adjustment = ADJUSTMENTS[
        str(
            market_config["daily_adjustment"]
        ).lower()
    ]
    feed = DATA_FEEDS[
        str(
            market_config["daily_feed"]
        ).lower()
    ]

    results: dict[str, Any] = {}
    for symbol in sorted(
        {
            normalized_symbol(value)
            for value in symbols
            if normalized_symbol(value)
        }
    ):
        existing: list[object] = []
        try:
            existing = bar_store.bars(symbol)
            last_timestamp = (
                as_utc_datetime(
                    existing[-1].get(
                        "timestamp"
                    )
                )
                if existing
                and isinstance(
                    existing[-1],
                    dict,
                )
                else None
            )
            start = (
                last_timestamp
                - timedelta(
                    days=max(overlap * 3, 7)
                )
                if last_timestamp is not None
                else current
                - timedelta(days=lookback)
            )
            response = call_api(
                "get_stock_daily_bars",
                clients.stock_data.get_stock_bars,
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=current,
                    adjustment=adjustment,
                    feed=feed,
                ),
            )
            incoming = _response_bars(
                response,
                symbol,
            )
            merged, invalid_count = (
                merge_daily_bars(
                    existing,
                    incoming,
                    retain=retain,
                )
            )
            status = (
                "success"
                if merged
                else "no_data"
            )
            payload = _snapshot_payload(
                symbol=symbol,
                status=status,
                bars=merged,
                previous_count=len(existing),
                fetched_count=len(incoming),
                invalid_count=invalid_count,
                minimum_bars=minimum,
                generated_at=current,
            )
        except Exception as error:
            if isinstance(error, V2Error):
                disposition = (
                    error.disposition()
                )
                error_payload = {
                    "code": disposition.code,
                    "message": (
                        disposition.message
                    ),
                }
            else:
                error_payload = {
                    "code": (
                        "DAILY_BAR_UPDATE_FAILED"
                    ),
                    "message": (
                        "单标的日线更新失败"
                    ),
                    "exception_type": (
                        error.__class__.__name__
                    ),
                }
            merged, invalid_count = (
                merge_daily_bars(
                    existing,
                    [],
                    retain=retain,
                )
            )
            payload = _snapshot_payload(
                symbol=symbol,
                status="failed",
                bars=merged,
                previous_count=len(existing),
                fetched_count=0,
                invalid_count=invalid_count,
                minimum_bars=minimum,
                generated_at=current,
                error=error_payload,
            )

        path = bar_store.save(
            symbol,
            payload,
        )
        results[symbol] = {
            "status": payload["status"],
            "path": str(path),
            "bar_count": payload["data"][
                "bar_count"
            ],
            "history_sufficient": payload[
                "data"
            ]["history_sufficient"],
            "warnings": payload["warnings"],
            "errors": payload["errors"],
        }

    return {
        "generated_at": iso_timestamp(current),
        "symbols": results,
        "success_count": sum(
            item["status"] == "success"
            for item in results.values()
        ),
        "no_data_count": sum(
            item["status"] == "no_data"
            for item in results.values()
        ),
        "failed_count": sum(
            item["status"] == "failed"
            for item in results.values()
        ),
    }
