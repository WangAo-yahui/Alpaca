from __future__ import annotations

from typing import Any

from v2.data._normalization import (
    enum_text,
    finite_float,
    normalized_symbol,
    read_field,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import StateValidationError


POSITION_NUMERIC_FIELDS = (
    "qty",
    "avg_entry_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "current_price",
    "lastday_price",
    "change_today",
)


def normalize_position(
    position: object,
) -> dict[str, Any]:
    symbol = normalized_symbol(
        read_field(position, "symbol")
    )
    if not symbol:
        raise StateValidationError(
            "持仓缺少symbol",
            code="POSITION_SYMBOL_MISSING",
        )

    values: dict[str, float] = {}
    for field in POSITION_NUMERIC_FIELDS:
        value = finite_float(
            read_field(position, field)
        )
        if value is None:
            raise StateValidationError(
                f"持仓字段缺失或无效：{field}",
                code="POSITION_FIELD_INVALID",
                details={
                    "symbol": symbol,
                    "field": field,
                },
            )
        values[field] = value

    available = finite_float(
        read_field(
            position,
            "qty_available",
            read_field(position, "qty"),
        )
    )
    if available is None:
        available = values["qty"]

    side = enum_text(
        read_field(position, "side"),
        default="long",
    ).lower()

    return {
        "symbol": symbol,
        "asset_id": enum_text(
            read_field(position, "asset_id")
        ),
        "side": side,
        "quantity": values["qty"],
        "available_quantity": available,
        "average_entry_price": values[
            "avg_entry_price"
        ],
        "market_value": values["market_value"],
        "cost_basis": values["cost_basis"],
        "unrealized_pl": values["unrealized_pl"],
        "current_price": values["current_price"],
        "lastday_price": values["lastday_price"],
        "change_today": values["change_today"],
    }


def normalize_positions(
    positions: object,
) -> list[dict[str, Any]]:
    if positions is None:
        return []
    try:
        raw_positions = list(positions)  # type: ignore[arg-type]
    except TypeError as error:
        raise StateValidationError(
            "Alpaca持仓响应必须是列表",
            code="POSITIONS_RESPONSE_INVALID",
        ) from error

    normalized = [
        normalize_position(position)
        for position in raw_positions
    ]
    normalized.sort(
        key=lambda item: item["symbol"]
    )
    return normalized


def fetch_positions(
    clients: AlpacaClients,
) -> list[dict[str, Any]]:
    clients.validate()
    raw = call_api(
        "get_all_positions",
        clients.trading.get_all_positions,
    )
    return normalize_positions(raw)
