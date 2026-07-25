"""刷新 WA Trader v2 Stage F 的最终订单前事实快照。

作用：在 execution output 之后重新获取账户、可用持仓、挂单、当日订单、报价、资产能力和市场阶段。
重要性：订单数量与请求规格只能依据这份更晚的快照；关键刷新失败会全局阻止批准，绝不回退到旧行情。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TypeVar

from v2.data._normalization import (
    compact_error,
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
from v2.data.quotes import fetch_latest_quotes, no_quote
from v2.exceptions import V2Error
from v2.models.orders import (
    PreTradeSnapshot,
    decimal_text,
    decimal_value,
)
from v2.profiles import OrderPolicy
from v2.runtime import CyclePaths, atomic_write_json


T = TypeVar("T")


@dataclass(frozen=True)
class PreTradeSnapshotResult:
    snapshot: PreTradeSnapshot
    symbols: tuple[str, ...]

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.snapshot.payload

    @property
    def order_planning_ready(self) -> bool:
        return self.snapshot.order_planning_ready


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
                code=f"{component.upper()}_REFRESH_FAILED",
                message=f"{component}订单前刷新失败",
                component=component,
                exception_type=error.__class__.__name__,
            )
        )
    return None


def _strictly_after(
    requested: datetime,
    earlier: object,
) -> datetime:
    normalized = str(earlier or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return requested.astimezone(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    current = requested.astimezone(timezone.utc)
    return (
        parsed + timedelta(microseconds=1)
        if current <= parsed
        else current
    )


def _money(value: object) -> str:
    return decimal_text(decimal_value(value))


def _account_payload(
    account: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if account is None:
        return None
    result = dict(account)
    for field in (
        "cash",
        "buying_power",
        "portfolio_value",
        "equity",
        "long_market_value",
        "short_market_value",
    ):
        result[field] = _money(account.get(field))
    return result


def _position_payload(
    position: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(position)
    for field in (
        "quantity",
        "available_quantity",
        "average_entry_price",
        "market_value",
        "cost_basis",
        "unrealized_pl",
        "current_price",
        "lastday_price",
        "change_today",
    ):
        result[field] = _money(position.get(field))
    return result


def _order_payload(
    order: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(order)
    for field in (
        "quantity",
        "notional",
        "filled_quantity",
        "limit_price",
        "stop_price",
    ):
        value = order.get(field)
        result[field] = (
            _money(value) if value is not None else None
        )
    quantity = (
        decimal_value(order.get("quantity"))
        if order.get("quantity") is not None
        else None
    )
    filled = decimal_value(
        order.get("filled_quantity", "0")
    )
    result["remaining_quantity"] = (
        decimal_text(max(quantity - filled, 0))
        if quantity is not None
        else None
    )
    return result


def _quote_payload(
    quote: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(quote)
    for field in (
        "bid_price",
        "bid_size",
        "ask_price",
        "ask_size",
        "midpoint",
        "spread",
        "spread_bps",
        "quote_age_seconds",
    ):
        value = quote.get(field)
        result[field] = (
            _money(value) if value is not None else None
        )
    return result


def _execution_symbols(
    execution_output: Mapping[str, Any],
    positions: list[Mapping[str, Any]],
    open_orders: list[Mapping[str, Any]],
) -> tuple[str, ...]:
    decisions = execution_output.get("decisions", [])
    return tuple(
        sorted(
            {
                str(item.get("symbol", "")).strip().upper()
                for item in [
                    *(
                        decisions
                        if isinstance(decisions, list)
                        else []
                    ),
                    *positions,
                    *open_orders,
                ]
                if isinstance(item, Mapping)
                and str(item.get("symbol", "")).strip()
            }
        )
    )


def create_pretrade_snapshot(
    paths: CyclePaths,
    clients: AlpacaClients,
    *,
    execution_output: Mapping[str, Any],
    order_policy: OrderPolicy,
    now: datetime | None = None,
    is_market_holiday: bool | None = None,
    asset_cache: AssetCache | None = None,
) -> PreTradeSnapshotResult:
    """Refresh, decimalize and atomically persist order-planning facts."""

    clients.validate()
    requested = now or utc_now()
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    retrieved = _strictly_after(
        requested,
        execution_output.get("generated_at"),
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
        open_orders if isinstance(open_orders, list) else []
    )
    normalized_today_orders = (
        today_orders if isinstance(today_orders, list) else []
    )
    symbols = _execution_symbols(
        execution_output,
        normalized_positions,
        normalized_open_orders,
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
    market_phase = (
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
            list(symbols),
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

    asset_result = (
        asset_cache or AssetCache(clients)
    ).get_many(list(symbols))
    warnings.extend(asset_result.errors)
    executable_symbols = {
        str(item.get("symbol", "")).strip().upper()
        for item in execution_output.get("decisions", [])
        if isinstance(item, Mapping)
        and item.get("execution_decision")
        in {"approve", "modify"}
    }
    missing_quotes = sorted(
        symbol
        for symbol in executable_symbols
        if normalized_quotes.get(symbol, {}).get(
            "status"
        )
        != "success"
    )
    missing_assets = sorted(
        executable_symbols - set(asset_result.assets)
    )
    if missing_quotes:
        errors.append(
            {
                "code": "PRETRADE_QUOTES_INCOMPLETE",
                "message": "可执行标的缺少有效最新报价",
                "component": "quotes",
                "symbols": ",".join(missing_quotes),
            }
        )
    if missing_assets:
        errors.append(
            {
                "code": "PRETRADE_ASSETS_INCOMPLETE",
                "message": "可执行标的缺少资产能力",
                "component": "assets",
                "symbols": ",".join(missing_assets),
            }
        )

    critical_components = {
        "account",
        "positions",
        "open_orders",
        "today_orders",
        "quotes",
        "assets",
    }
    order_planning_ready = not any(
        item.get("component") in critical_components
        for item in errors
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "pretrade_snapshot",
        "profile_id": paths.profile_id,
        "strategy_id": paths.strategy_id,
        "strategy_version": paths.strategy_version,
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "execution_generated_at": (
            execution_output.get("generated_at")
        ),
        "retrieved_at": iso_timestamp(retrieved),
        "market_phase": market_phase,
        "account": _account_payload(
            account
            if isinstance(account, Mapping)
            else None
        ),
        "positions": [
            _position_payload(item)
            for item in normalized_positions
        ],
        "open_orders": [
            _order_payload(item)
            for item in normalized_open_orders
        ],
        "today_orders": [
            _order_payload(item)
            for item in normalized_today_orders
        ],
        "quotes": {
            symbol: _quote_payload(quote)
            for symbol, quote in sorted(
                normalized_quotes.items()
            )
        },
        "assets": {
            symbol: dict(asset)
            for symbol, asset in sorted(
                asset_result.assets.items()
            )
        },
        "broker_capabilities": dict(
            order_policy.settings.get(
                "broker_capabilities",
                {},
            )
        ),
        "order_policy": order_policy.reference,
        "order_planning_ready": order_planning_ready,
        "data_quality": {
            "critical_error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
        },
    }
    atomic_write_json(
        paths.pretrade_snapshot,
        payload,
    )
    return PreTradeSnapshotResult(
        snapshot=PreTradeSnapshot.from_payload(
            payload
        ),
        symbols=symbols,
    )
