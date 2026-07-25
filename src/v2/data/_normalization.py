from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


UTC = ZoneInfo("UTC")


def read_field(
    value: object,
    name: str,
    default: Any = None,
) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def enum_text(
    value: object,
    *,
    default: str = "",
) -> str:
    if value is None:
        return default
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    return text if text else default


def normalized_symbol(value: object) -> str:
    return enum_text(value).upper()


def finite_float(
    value: object,
) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def finite_int(
    value: object,
) -> int | None:
    number = finite_float(value)
    if number is None:
        return None
    return int(number)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_timestamp(
    value: object | None,
    *,
    default: datetime | None = None,
) -> str | None:
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError:
            return None
    elif default is not None:
        parsed = default
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def as_utc_datetime(
    value: object,
) -> datetime | None:
    text = iso_timestamp(value)
    if text is None:
        return None
    return datetime.fromisoformat(text)


def compact_error(
    *,
    code: str,
    message: str,
    component: str,
    exception_type: str | None = None,
) -> dict[str, str]:
    result = {
        "code": code,
        "message": message,
        "component": component,
    }
    if exception_type:
        result["exception_type"] = exception_type
    return result
