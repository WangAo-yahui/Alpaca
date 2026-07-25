"""刷新 WA Trader v2 Stage E 的执行级账户与市场快照。

作用：在组合方案之后重新获取账户、持仓、订单、报价、成交、分钟线和资产能力。
重要性：第三阶段只能依据这份更新后的事实形成执行意图；缺失或过期数据不得伪装为零值。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from alpaca.data.requests import (
    StockLatestTradeRequest,
)

from v2.data._normalization import (
    compact_error,
    finite_float,
    iso_timestamp,
    normalized_symbol,
    read_field,
    utc_now,
)
from v2.data.account import fetch_account
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.data.assets import AssetCache
from v2.data.intraday import (
    determine_market_phase,
    fetch_intraday_summaries,
    fetch_market_holiday_status,
)
from v2.data.orders import (
    fetch_open_orders,
    fetch_today_orders,
)
from v2.data.positions import fetch_positions
from v2.data.quotes import (
    fetch_latest_quotes,
    no_quote,
)
from v2.data.snapshots import (
    estimate_open_order_reserve,
)
from v2.exceptions import V2Error
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
)


T = TypeVar("T")


@dataclass(frozen=True)
class ExecutionSnapshotResult:
    payload: dict[str, Any]
    execution_ready: bool
    symbols: tuple[str, ...]


def _safe_fetch(
    component: str,
    function: Callable[[], T],
    *,
    errors: list[dict[str, str]],
) -> T | None:
    try:
        return function()
    except V2Error as error:
        disposition = error.disposition()
        errors.append(
            compact_error(
                code=disposition.code,
                message=disposition.message,
                component=component,
                exception_type=str(
                    disposition.details.get(
                        "exception_type",
                        "",
                    )
                )
                or None,
            )
        )
    except Exception as error:
        errors.append(
            compact_error(
                code=(
                    f"{component.upper()}_"
                    "NORMALIZATION_FAILED"
                ),
                message=(
                    f"{component}执行级数据规范化失败"
                ),
                component=component,
                exception_type=(
                    error.__class__.__name__
                ),
            )
        )
    return None


def _mapping(response: object) -> dict[str, object]:
    data = (
        response
        if isinstance(response, dict)
        else read_field(response, "data", {})
    )
    if not isinstance(data, dict):
        return {}
    return {
        normalized_symbol(symbol): value
        for symbol, value in data.items()
    }


def normalize_latest_trade(
    symbol: str,
    trade: object | None,
) -> dict[str, Any]:
    normalized = normalized_symbol(symbol)
    price = finite_float(
        read_field(trade, "price")
    )
    size = finite_float(
        read_field(trade, "size")
    )
    timestamp = iso_timestamp(
        read_field(trade, "timestamp")
    )
    if (
        trade is None
        or price is None
        or price <= 0
        or timestamp is None
    ):
        return {
            "symbol": normalized,
            "status": "no_data",
            "price": None,
            "size": None,
            "timestamp": None,
        }
    return {
        "symbol": normalized,
        "status": "success",
        "price": price,
        "size": size,
        "timestamp": timestamp,
    }


def fetch_latest_trades(
    clients: AlpacaClients,
    symbols: list[str],
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
        "get_stock_latest_trade",
        clients.stock_data.get_stock_latest_trade,
        StockLatestTradeRequest(
            symbol_or_symbols=unique
        ),
    )
    mapping = _mapping(response)
    return {
        symbol: normalize_latest_trade(
            symbol,
            mapping.get(symbol),
        )
        for symbol in unique
    }


def _strictly_after(
    current: datetime,
    earlier: str,
) -> datetime:
    normalized = str(earlier).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return current
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    parsed = parsed.astimezone(timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    if current_utc <= parsed:
        return parsed + timedelta(
            microseconds=1
        )
    return current_utc


def create_execution_snapshot(
    paths: CyclePaths,
    clients: AlpacaClients,
    *,
    portfolio_output: dict[str, Any],
    now: datetime | None = None,
    minute_window: int = 60,
    is_market_holiday: bool | None = None,
    asset_cache: AssetCache | None = None,
) -> ExecutionSnapshotResult:
    """Refresh execution facts after portfolio output and persist atomically."""

    clients.validate()
    requested = now or utc_now()
    if requested.tzinfo is None:
        requested = requested.replace(
            tzinfo=timezone.utc
        )
    retrieved = _strictly_after(
        requested,
        str(
            portfolio_output.get(
                "generated_at",
                "",
            )
        ),
    )
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    account = _safe_fetch(
        "account",
        lambda: fetch_account(
            clients,
            retrieved_at=retrieved,
        ),
        errors=errors,
    )
    positions = _safe_fetch(
        "positions",
        lambda: fetch_positions(clients),
        errors=errors,
    )
    open_orders = _safe_fetch(
        "open_orders",
        lambda: fetch_open_orders(clients),
        errors=errors,
    )
    today_orders = _safe_fetch(
        "today_orders",
        lambda: fetch_today_orders(
            clients,
            now=retrieved,
        ),
        errors=errors,
    )
    normalized_positions = (
        positions if isinstance(positions, list) else []
    )
    normalized_open_orders = (
        open_orders
        if isinstance(open_orders, list)
        else []
    )
    normalized_today_orders = (
        today_orders
        if isinstance(today_orders, list)
        else []
    )
    decisions = portfolio_output.get(
        "decisions",
        [],
    )
    symbols = sorted(
        {
            str(item.get("symbol", "")).upper()
            for item in [
                *(
                    decisions
                    if isinstance(decisions, list)
                    else []
                ),
                *normalized_positions,
                *normalized_open_orders,
            ]
            if isinstance(item, dict)
            and item.get("symbol")
        }
    )

    holiday = is_market_holiday
    if holiday is None:
        holiday = _safe_fetch(
            "market_calendar",
            lambda: fetch_market_holiday_status(
                clients,
                retrieved,
            ),
            errors=warnings,
        )
    phase = (
        determine_market_phase(
            retrieved,
            is_market_holiday=bool(holiday),
        )
        if holiday is not None
        else "unknown"
    )
    quotes = _safe_fetch(
        "quotes",
        lambda: fetch_latest_quotes(
            clients,
            symbols,
            now=retrieved,
        ),
        errors=errors,
    )
    normalized_quotes = (
        quotes if isinstance(quotes, dict) else {}
    )
    for symbol in symbols:
        normalized_quotes.setdefault(
            symbol,
            no_quote(symbol),
        )
    trades = _safe_fetch(
        "latest_trades",
        lambda: fetch_latest_trades(
            clients,
            symbols,
        ),
        errors=errors,
    )
    normalized_trades = (
        trades if isinstance(trades, dict) else {}
    )
    for symbol in symbols:
        normalized_trades.setdefault(
            symbol,
            normalize_latest_trade(
                symbol,
                None,
            ),
        )
    intraday = _safe_fetch(
        "minute_bars",
        lambda: fetch_intraday_summaries(
            clients,
            symbols,
            start=(
                retrieved
                - timedelta(
                    minutes=max(
                        minute_window + 10,
                        70,
                    )
                )
            ),
            end=retrieved,
            window=minute_window,
            market_phase=phase,
        ),
        errors=errors,
    )
    normalized_intraday = (
        intraday
        if isinstance(intraday, dict)
        else {
            symbol: {
                "symbol": symbol,
                "status": "no_data",
                "market_phase": phase,
                "window_status": "no_data",
                "bar_count": 0,
                "bars": [],
                "summary": None,
            }
            for symbol in symbols
        }
    )
    asset_result = (
        asset_cache or AssetCache(clients)
    ).get_many(symbols)
    warnings.extend(asset_result.errors)
    reserved, reserve_warnings = (
        estimate_open_order_reserve(
            normalized_open_orders
        )
    )
    warnings.extend(reserve_warnings)
    failed_components = {
        item["component"]
        for item in errors
    }
    critical = {
        "account",
        "positions",
        "open_orders",
        "today_orders",
    }
    execution_ready = not bool(
        failed_components & critical
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "execution_snapshot",
        "profile_id": paths.profile_id,
        "strategy_id": paths.strategy_id,
        "strategy_version": (
            paths.strategy_version
        ),
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "portfolio_generated_at": (
            portfolio_output.get("generated_at")
        ),
        "retrieved_at": iso_timestamp(
            retrieved
        ),
        "market_phase": phase,
        "account": account,
        "positions": normalized_positions,
        "open_orders": normalized_open_orders,
        "today_orders": normalized_today_orders,
        "quotes": normalized_quotes,
        "latest_trades": normalized_trades,
        "intraday": normalized_intraday,
        "assets": asset_result.assets,
        "capital": {
            "open_order_reserved_estimate": (
                reserved
            )
        },
        "broker_extended_hours_capability": {
            "supported": True,
            "supported_phases": [
                "before_market_open",
                "after_market_close",
            ],
            "requires_limit_intent": True,
            "time_in_force": "day",
        },
        "data_quality": {
            "execution_ready": execution_ready,
            "quotes_complete": all(
                normalized_quotes[symbol].get(
                    "status"
                )
                == "success"
                for symbol in symbols
            ),
            "trades_complete": all(
                normalized_trades[symbol].get(
                    "status"
                )
                == "success"
                for symbol in symbols
            ),
            "assets_complete": (
                not asset_result.errors
            ),
            "errors": errors,
            "warnings": warnings,
        },
    }
    atomic_write_json(
        paths.execution_snapshot,
        payload,
    )
    return ExecutionSnapshotResult(
        payload=payload,
        execution_ready=execution_ready,
        symbols=tuple(symbols),
    )
