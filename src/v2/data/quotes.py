"""读取并规范化 Alpaca 最新报价及价差、时效性。

作用：计算 bid、ask、mid、spread 与 quote age，明确缺失和 crossed quote。
重要性：价格陈旧或价差异常时必须阻止订单，不能把缺失报价当作零价格。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.requests import (
    CryptoLatestQuoteRequest,
    StockLatestQuoteRequest,
)

from v2.data._normalization import (
    as_utc_datetime,
    crypto_request_symbol,
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


def no_quote(
    symbol: str,
    *,
    status: str = "no_data",
    data_feed: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": normalized_symbol(symbol),
        "status": status,
        "data_feed": data_feed,
        "bid_exchange": None,
        "ask_exchange": None,
        "bid_price": None,
        "bid_size": None,
        "ask_price": None,
        "ask_size": None,
        "midpoint": None,
        "spread": None,
        "spread_bps": None,
        "quote_timestamp": None,
        "quote_age_seconds": None,
    }


def normalize_quote(
    symbol: str,
    quote: object | None,
    *,
    now: datetime | None = None,
    data_feed: str | None = None,
) -> dict[str, Any]:
    normalized = normalized_symbol(symbol)
    if quote is None:
        return no_quote(
            normalized,
            data_feed=data_feed,
        )

    bid = finite_float(
        read_field(quote, "bid_price")
    )
    ask = finite_float(
        read_field(quote, "ask_price")
    )
    bid_size = finite_float(
        read_field(quote, "bid_size")
    )
    ask_size = finite_float(
        read_field(quote, "ask_size")
    )
    timestamp = as_utc_datetime(
        read_field(quote, "timestamp")
    )
    bid_exchange = str(
        read_field(quote, "bid_exchange", "")
        or ""
    ).strip() or None
    ask_exchange = str(
        read_field(quote, "ask_exchange", "")
        or ""
    ).strip() or None

    if (
        bid is None
        or ask is None
        or bid <= 0
        or ask <= 0
        or ask < bid
        or timestamp is None
    ):
        return no_quote(
            normalized,
            status="invalid_data",
            data_feed=data_feed,
        )

    midpoint = (bid + ask) / 2
    spread = ask - bid
    spread_bps = (
        spread / midpoint * 10_000
        if midpoint > 0
        else None
    )

    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timestamp.tzinfo
        )
    age = max(
        0.0,
        (
            current.astimezone(timestamp.tzinfo)
            - timestamp
        ).total_seconds(),
    )

    return {
        "symbol": normalized,
        "status": "success",
        "data_feed": data_feed,
        "bid_exchange": bid_exchange,
        "ask_exchange": ask_exchange,
        "bid_price": bid,
        "bid_size": bid_size,
        "ask_price": ask,
        "ask_size": ask_size,
        "midpoint": midpoint,
        "spread": spread,
        "spread_bps": spread_bps,
        "quote_timestamp": iso_timestamp(
            timestamp
        ),
        "quote_age_seconds": age,
    }


def _quote_mapping(
    response: object,
) -> dict[str, object]:
    if isinstance(response, dict):
        return {
            normalized_symbol(key): value
            for key, value in response.items()
        }
    data = read_field(response, "data")
    if isinstance(data, dict):
        return {
            normalized_symbol(key): value
            for key, value in data.items()
        }
    return {}


def _canonical_crypto_symbol(value: object) -> str:
    return normalized_symbol(value).replace("/", "")


def _crypto_quote_mapping(
    response: object,
) -> dict[str, object]:
    if isinstance(response, dict):
        data = response
    else:
        data = read_field(response, "data", {})
    if not isinstance(data, dict):
        return {}
    return {
        _canonical_crypto_symbol(key): value
        for key, value in data.items()
    }


def fetch_latest_quotes(
    clients: AlpacaClients,
    symbols: list[str],
    *,
    now: datetime | None = None,
    feed: DataFeed | None = None,
    crypto_symbols: list[str] | None = None,
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
    crypto = {
        _canonical_crypto_symbol(symbol)
        for symbol in (crypto_symbols or [])
    }
    stock = [
        symbol
        for symbol in unique
        if _canonical_crypto_symbol(symbol)
        not in crypto
    ]
    mapping: dict[str, object] = {}
    stock_feed = (
        feed.value
        if feed is not None
        else "subscription_default"
    )
    if stock:
        response = call_api(
            "get_stock_latest_quote",
            clients.stock_data.get_stock_latest_quote,
            StockLatestQuoteRequest(
                symbol_or_symbols=stock,
                feed=feed,
            ),
        )
        mapping.update(_quote_mapping(response))
    if crypto:
        if clients.crypto_data is None:
            raise ValueError(
                "Alpaca crypto data client不能为空"
            )
        response = call_api(
            "get_crypto_latest_quote",
            clients.crypto_data.get_crypto_latest_quote,
            CryptoLatestQuoteRequest(
                symbol_or_symbols=[
                    crypto_request_symbol(symbol)
                    for symbol in sorted(crypto)
                ],
            ),
        )
        mapping.update(
            _crypto_quote_mapping(response)
        )
    return {
        symbol: normalize_quote(
            symbol,
            mapping.get(
                _canonical_crypto_symbol(symbol)
                if _canonical_crypto_symbol(symbol)
                in crypto
                else symbol
            ),
            now=now,
            data_feed=(
                "crypto"
                if _canonical_crypto_symbol(symbol)
                in crypto
                else stock_feed
            ),
        )
        for symbol in unique
    }
