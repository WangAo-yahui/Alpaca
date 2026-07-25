"""创建并原子保存 WA Trader v2 的基础账户与市场快照。

作用：汇总账户、持仓、订单、资产和行情错误，形成后续决策的客观输入。
重要性：即使部分读取失败也必须保存证据，并在关键事实缺失时阻止进入决策或交易。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, TypeVar

from v2.data._normalization import (
    compact_error,
    finite_float,
    iso_timestamp,
    utc_now,
)
from v2.data.account import fetch_account
from v2.data.alpaca_client import AlpacaClients
from v2.data.assets import AssetCache
from v2.data.intraday import (
    determine_market_phase,
    fetch_market_holiday_status,
)
from v2.data.orders import (
    fetch_open_orders,
    fetch_today_orders,
)
from v2.data.positions import fetch_positions
from v2.exceptions import V2Error
from v2.runtime import CyclePaths, atomic_write_json


T = TypeVar("T")


@dataclass(frozen=True)
class BaseSnapshotResult:
    payload: dict[str, Any]
    decision_ready: bool


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
                    f"{component}数据规范化失败"
                ),
                component=component,
                exception_type=(
                    error.__class__.__name__
                ),
            )
        )
    return None


def estimate_open_order_reserve(
    orders: list[dict[str, Any]],
) -> tuple[float, list[dict[str, str]]]:
    reserved = 0.0
    warnings: list[dict[str, str]] = []
    for order in orders:
        if order.get("side") != "buy":
            continue
        quantity = finite_float(
            order.get("quantity")
        )
        filled = finite_float(
            order.get("filled_quantity")
        ) or 0.0
        price = finite_float(
            order.get("limit_price")
        )
        if price is None:
            price = finite_float(
                order.get("stop_price")
            )
        notional = finite_float(
            order.get("notional")
        )
        if quantity is None and notional is not None:
            reserved += notional
            continue
        if quantity is None or price is None:
            warnings.append(
                compact_error(
                    code="ORDER_RESERVE_UNCERTAIN",
                    message=(
                        "买单缺少可用于预留估算的"
                        "数量或价格"
                    ),
                    component=(
                        "open_order:"
                        + str(
                            order.get(
                                "broker_order_id",
                                "",
                            )
                        )
                    ),
                )
            )
            continue
        remaining = max(0.0, quantity - filled)
        reserved += remaining * price
    return reserved, warnings


def create_base_snapshot(
    paths: CyclePaths,
    clients: AlpacaClients,
    *,
    now: datetime | None = None,
    is_market_holiday: bool | None = None,
    asset_cache: AssetCache | None = None,
) -> BaseSnapshotResult:
    """Fetch and atomically persist the cycle's risk-critical base data."""

    retrieved = now or utc_now()
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    holiday_status = is_market_holiday
    if holiday_status is None:
        try:
            holiday_status = (
                fetch_market_holiday_status(
                    clients,
                    retrieved,
                )
            )
        except V2Error as error:
            disposition = error.disposition()
            warnings.append(
                compact_error(
                    code=disposition.code,
                    message=(
                        "无法确认市场日历，"
                        "market_phase降级为unknown"
                    ),
                    component="market_calendar",
                    exception_type=str(
                        disposition.details.get(
                            "exception_type",
                            "",
                        )
                    )
                    or None,
                )
            )

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
        positions
        if isinstance(positions, list)
        else []
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
    symbols = sorted(
        {
            str(item.get("symbol", ""))
            for item in [
                *normalized_positions,
                *normalized_open_orders,
                *normalized_today_orders,
            ]
            if item.get("symbol")
        }
    )
    repository = asset_cache or AssetCache(
        clients
    )
    assets_result = repository.get_many(symbols)
    warnings.extend(assets_result.errors)

    reserved, reserve_warnings = (
        estimate_open_order_reserve(
            normalized_open_orders
        )
    )
    warnings.extend(reserve_warnings)

    cash = (
        finite_float(account.get("cash"))
        if isinstance(account, dict)
        else None
    )
    buying_power = (
        finite_float(
            account.get("buying_power")
        )
        if isinstance(account, dict)
        else None
    )
    allocatable = (
        max(
            0.0,
            min(cash, buying_power) - reserved,
        )
        if cash is not None
        and buying_power is not None
        else None
    )

    failed_components = {
        item["component"]
        for item in errors
    }
    critical_components = {
        "account",
        "positions",
        "open_orders",
        "today_orders",
    }
    decision_ready = not bool(
        failed_components & critical_components
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "retrieved_at": iso_timestamp(
            retrieved
        ),
        "market_phase": (
            determine_market_phase(
                retrieved,
                is_market_holiday=bool(
                    holiday_status
                ),
            )
            if holiday_status is not None
            else "unknown"
        ),
        "account": account,
        "positions": normalized_positions,
        "open_orders": normalized_open_orders,
        "today_orders": normalized_today_orders,
        "assets": assets_result.assets,
        "capital": {
            "cash": cash,
            "buying_power": buying_power,
            "open_order_reserved_estimate": (
                reserved
            ),
            "allocatable_capital_estimate": (
                allocatable
            ),
        },
        "data_quality": {
            "account_fresh": (
                "account"
                not in failed_components
            ),
            "positions_fresh": (
                "positions"
                not in failed_components
            ),
            "orders_fresh": not bool(
                {
                    "open_orders",
                    "today_orders",
                }
                & failed_components
            ),
            "assets_complete": (
                not assets_result.errors
            ),
            "decision_ready": decision_ready,
            "errors": errors,
            "warnings": warnings,
        },
    }
    atomic_write_json(
        paths.base_snapshot,
        payload,
    )
    return BaseSnapshotResult(
        payload=payload,
        decision_ready=decision_ready,
    )
