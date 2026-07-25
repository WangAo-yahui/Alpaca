from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from v2.data._normalization import (
    enum_text,
    finite_float,
    iso_timestamp,
    read_field,
    utc_now,
)
from v2.data.alpaca_client import (
    AlpacaClients,
    call_api,
)
from v2.exceptions import StateValidationError


REQUIRED_NUMERIC_FIELDS = (
    "cash",
    "buying_power",
    "portfolio_value",
    "equity",
)


def _account_id_hash(value: object) -> str:
    text = enum_text(value)
    if not text:
        raise StateValidationError(
            "Alpaca账户缺少account id",
            code="ACCOUNT_ID_MISSING",
        )
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def normalize_account(
    account: object,
    *,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    numbers: dict[str, float] = {}
    for field in REQUIRED_NUMERIC_FIELDS:
        value = finite_float(
            read_field(account, field)
        )
        if value is None:
            raise StateValidationError(
                f"账户字段缺失或无效：{field}",
                code="ACCOUNT_FIELD_INVALID",
                details={"field": field},
            )
        numbers[field] = value

    long_market_value = finite_float(
        read_field(
            account,
            "long_market_value",
        )
    )
    short_market_value = finite_float(
        read_field(
            account,
            "short_market_value",
        )
    )
    status = enum_text(
        read_field(account, "status")
    )
    if not status:
        raise StateValidationError(
            "账户状态缺失",
            code="ACCOUNT_STATUS_MISSING",
        )

    return {
        "account_id_hash": _account_id_hash(
            read_field(account, "id")
        ),
        "status": status.upper(),
        "trading_blocked": bool(
            read_field(
                account,
                "trading_blocked",
                False,
            )
        ),
        "account_blocked": bool(
            read_field(
                account,
                "account_blocked",
                False,
            )
        ),
        "trade_suspended_by_user": bool(
            read_field(
                account,
                "trade_suspended_by_user",
                False,
            )
        ),
        **numbers,
        "long_market_value": (
            long_market_value
            if long_market_value is not None
            else 0.0
        ),
        "short_market_value": (
            short_market_value
            if short_market_value is not None
            else 0.0
        ),
        "currency": enum_text(
            read_field(
                account,
                "currency",
                "USD",
            ),
            default="USD",
        ).upper(),
        "retrieved_at": iso_timestamp(
            retrieved_at,
            default=utc_now(),
        ),
        "source": "alpaca_paper",
    }


def fetch_account(
    clients: AlpacaClients,
    *,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    clients.validate()
    raw = call_api(
        "get_account",
        clients.trading.get_account,
    )
    return normalize_account(
        raw,
        retrieved_at=retrieved_at,
    )
