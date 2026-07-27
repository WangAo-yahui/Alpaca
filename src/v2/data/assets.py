"""读取并缓存 Alpaca 资产能力。

作用：规范化交易能力、资产类别、状态，以及 Crypto 数量和价格最小步进。
重要性：订单构建与最终校验必须依据券商当前能力与精度，不能假设标的始终可交易或使用统一小数位。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from v2.data._normalization import (
    compact_error,
    enum_text,
    normalized_symbol,
    read_field,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import V2Error


def _positive_decimal_text(
    value: object,
) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    try:
        result = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return format(result.normalize(), "f")


def normalize_asset(
    asset: object,
) -> dict[str, Any]:
    symbol = normalized_symbol(
        read_field(asset, "symbol")
    )
    if not symbol:
        raise ValueError("资产缺少symbol")
    raw_attributes = read_field(
        asset, "attributes", []
    )
    try:
        attributes = sorted(
            {
                enum_text(item).lower()
                for item in raw_attributes
                if enum_text(item)
            }
        )
    except TypeError:
        attributes = []
    raw_overnight_tradable = read_field(
        asset, "overnight_tradable", None
    )
    raw_overnight_halted = read_field(
        asset, "overnight_halted", None
    )
    return {
        "symbol": symbol,
        "tradable": bool(
            read_field(asset, "tradable", False)
        ),
        "fractionable": bool(
            read_field(
                asset,
                "fractionable",
                False,
            )
        ),
        "shortable": bool(
            read_field(asset, "shortable", False)
        ),
        "easy_to_borrow": bool(
            read_field(
                asset,
                "easy_to_borrow",
                False,
            )
        ),
        "exchange": enum_text(
            read_field(asset, "exchange")
        ).upper(),
        "asset_class": enum_text(
            read_field(asset, "asset_class")
        ).lower(),
        "status": enum_text(
            read_field(asset, "status")
        ).lower(),
        "min_order_size": _positive_decimal_text(
            read_field(asset, "min_order_size")
        ),
        "min_trade_increment": _positive_decimal_text(
            read_field(
                asset,
                "min_trade_increment",
            )
        ),
        "price_increment": _positive_decimal_text(
            read_field(asset, "price_increment")
        ),
        "attributes": attributes,
        "overnight_tradable": (
            bool(raw_overnight_tradable)
            if raw_overnight_tradable is not None
            else "overnight_tradable" in attributes
        ),
        "overnight_halted": (
            bool(raw_overnight_halted)
            if raw_overnight_halted is not None
            else "overnight_halted" in attributes
        ),
    }


@dataclass
class AssetFetchResult:
    assets: dict[str, dict[str, Any]]
    errors: list[dict[str, str]]


@dataclass
class AssetCache:
    clients: AlpacaClients
    values: dict[
        str,
        dict[str, Any],
    ] = field(default_factory=dict)

    def get(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        normalized = normalized_symbol(symbol)
        if not normalized:
            raise ValueError(
                "symbol不能为空"
            )
        if normalized not in self.values:
            raw = call_api(
                "get_asset",
                self.clients.trading.get_asset,
                normalized,
            )
            self.values[normalized] = (
                normalize_asset(raw)
            )
        return self.values[normalized]

    def get_many(
        self,
        symbols: list[str],
    ) -> AssetFetchResult:
        assets: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        unique_symbols = sorted(
            {
                normalized_symbol(symbol)
                for symbol in symbols
                if normalized_symbol(symbol)
            }
        )
        for symbol in unique_symbols:
            try:
                assets[symbol] = self.get(symbol)
            except V2Error as error:
                disposition = error.disposition()
                errors.append(
                    compact_error(
                        code=disposition.code,
                        message=disposition.message,
                        component=f"asset:{symbol}",
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
                        code="ASSET_NORMALIZATION_FAILED",
                        message=(
                            f"资产规范化失败：{symbol}"
                        ),
                        component=f"asset:{symbol}",
                        exception_type=(
                            error.__class__.__name__
                        ),
                    )
                )
        return AssetFetchResult(
            assets=assets,
            errors=errors,
        )
