from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from v2.data._normalization import (
    enum_text,
    finite_float,
    iso_timestamp,
    normalized_symbol,
    read_field,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import StateValidationError


NEW_YORK_TZ = ZoneInfo("America/New_York")
SYSTEM_CLIENT_ORDER_PREFIX = "wa2-"


def normalize_order(
    order: object,
) -> dict[str, Any]:
    symbol = normalized_symbol(
        read_field(order, "symbol")
    )
    if not symbol:
        raise StateValidationError(
            "订单缺少symbol",
            code="ORDER_SYMBOL_MISSING",
        )

    quantity = finite_float(
        read_field(order, "qty")
    )
    notional = finite_float(
        read_field(order, "notional")
    )
    filled_quantity = finite_float(
        read_field(order, "filled_qty", 0)
    )
    return {
        "broker_order_id": enum_text(
            read_field(order, "id")
        ),
        "client_order_id": enum_text(
            read_field(order, "client_order_id")
        ),
        "symbol": symbol,
        "side": enum_text(
            read_field(order, "side")
        ).lower(),
        "type": enum_text(
            read_field(order, "type")
        ).lower(),
        "time_in_force": enum_text(
            read_field(order, "time_in_force")
        ).lower(),
        "quantity": quantity,
        "notional": notional,
        "filled_quantity": (
            filled_quantity
            if filled_quantity is not None
            else 0.0
        ),
        "limit_price": finite_float(
            read_field(order, "limit_price")
        ),
        "stop_price": finite_float(
            read_field(order, "stop_price")
        ),
        "status": enum_text(
            read_field(order, "status")
        ).lower(),
        "extended_hours": bool(
            read_field(
                order,
                "extended_hours",
                False,
            )
        ),
        "submitted_at": iso_timestamp(
            read_field(order, "submitted_at")
        ),
        "updated_at": iso_timestamp(
            read_field(order, "updated_at")
        ),
    }


def normalize_orders(
    orders: object,
) -> list[dict[str, Any]]:
    if orders is None:
        return []
    try:
        raw_orders = list(orders)  # type: ignore[arg-type]
    except TypeError as error:
        raise StateValidationError(
            "Alpaca订单响应必须是列表",
            code="ORDERS_RESPONSE_INVALID",
        ) from error

    normalized = [
        normalize_order(order)
        for order in raw_orders
    ]
    normalized.sort(
        key=lambda item: (
            item["submitted_at"] or "",
            item["symbol"],
            item["broker_order_id"],
        )
    )
    return normalized


def _fetch_orders(
    clients: AlpacaClients,
    request: GetOrdersRequest,
    operation: str,
) -> list[dict[str, Any]]:
    clients.validate()
    raw = call_api(
        operation,
        clients.trading.get_orders,
        filter=request,
    )
    return normalize_orders(raw)


def fetch_open_orders(
    clients: AlpacaClients,
) -> list[dict[str, Any]]:
    return _fetch_orders(
        clients,
        GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            limit=500,
            nested=True,
        ),
        "get_open_orders",
    )


def fetch_today_orders(
    clients: AlpacaClients,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(NEW_YORK_TZ)
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TZ
        )
    current = current.astimezone(NEW_YORK_TZ)
    day_start = datetime.combine(
        current.date(),
        time.min,
        tzinfo=NEW_YORK_TZ,
    )
    return _fetch_orders(
        clients,
        GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            after=day_start,
            limit=500,
            nested=True,
        ),
        "get_today_orders",
    )


def fetch_recent_orders(
    clients: AlpacaClients,
    *,
    now: datetime | None = None,
    lookback_days: int = 7,
) -> list[dict[str, Any]]:
    if lookback_days <= 0:
        raise ValueError(
            "lookback_days必须大于0"
        )
    current = now or datetime.now(NEW_YORK_TZ)
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TZ
        )
    return _fetch_orders(
        clients,
        GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            after=current - timedelta(
                days=lookback_days
            ),
            limit=500,
            nested=True,
        ),
        "get_recent_orders",
    )


def system_submitted_orders(
    orders: list[dict[str, Any]],
    *,
    prefix: str = SYSTEM_CLIENT_ORDER_PREFIX,
) -> list[dict[str, Any]]:
    return [
        order
        for order in orders
        if str(
            order.get("client_order_id", "")
        ).startswith(prefix)
    ]


def fetch_system_submitted_orders(
    clients: AlpacaClients,
    *,
    now: datetime | None = None,
    lookback_days: int = 30,
    prefix: str = SYSTEM_CLIENT_ORDER_PREFIX,
) -> list[dict[str, Any]]:
    return system_submitted_orders(
        fetch_recent_orders(
            clients,
            now=now,
            lookback_days=lookback_days,
        ),
        prefix=prefix,
    )
