from __future__ import annotations

from dataclasses import dataclass, field
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


def normalize_asset(
    asset: object,
) -> dict[str, Any]:
    symbol = normalized_symbol(
        read_field(asset, "symbol")
    )
    if not symbol:
        raise ValueError("资产缺少symbol")
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
