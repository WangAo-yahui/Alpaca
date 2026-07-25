from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from typing import TYPE_CHECKING

from alpaca.data.historical import (
    StockHistoricalDataClient,
)
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

from v2.exceptions import (
    BrokerUnavailableError,
    ConfigurationError,
    LiveTradingRejected,
    V2Error,
)
from v2.runtime import get_project_root

if TYPE_CHECKING:
    from v2.profiles import Profile


T = TypeVar("T")

API_KEY_NAMES = (
    "ALPACA_API_KEY",
    "APCA_API_KEY_ID",
)
SECRET_KEY_NAMES = (
    "ALPACA_SECRET_KEY",
    "APCA_API_SECRET_KEY",
)


@dataclass(frozen=True)
class AlpacaClients:
    trading: Any
    stock_data: Any
    paper: bool = True

    def validate(self) -> None:
        if not self.paper:
            raise LiveTradingRejected()
        if self.trading is None:
            raise ConfigurationError(
                "Alpaca trading client不能为空"
            )
        if self.stock_data is None:
            raise ConfigurationError(
                "Alpaca stock data client不能为空"
            )


def _first_environment_value(
    environ: Mapping[str, str],
    names: tuple[str, ...],
) -> str:
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    return ""


def _paper_environment_enabled(
    environ: Mapping[str, str],
) -> bool:
    raw = environ.get(
        "ALPACA_PAPER",
        "true",
    ).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        "ALPACA_PAPER必须是布尔值",
        details={"variable": "ALPACA_PAPER"},
    )


def create_alpaca_clients(
    *,
    paper: bool = True,
    live: bool = False,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
    trading_factory: Callable[..., Any] = TradingClient,
    stock_data_factory: Callable[
        ..., Any
    ] = StockHistoricalDataClient,
    profile: "Profile | None" = None,
) -> AlpacaClients:
    """Create the only Alpaca client pair used by v2.

    Supplying ``environ`` disables dotenv loading, which keeps tests hermetic.
    Neither credential value is ever included in an error message or details.
    """

    if live or not paper:
        raise LiveTradingRejected()

    if environ is None:
        root = (
            project_root.expanduser().resolve()
            if project_root is not None
            else get_project_root()
        )
        load_dotenv(
            dotenv_path=root / ".env",
            override=False,
        )
        source_environment: Mapping[str, str] = (
            os.environ
        )
    else:
        source_environment = environ

    if not _paper_environment_enabled(
        source_environment
    ):
        raise LiveTradingRejected()

    if profile is not None:
        if profile.environment != "paper":
            raise LiveTradingRejected()
        api_names = (
            profile.credential_key_env,
        )
        secret_names = (
            profile.credential_secret_env,
        )
    else:
        api_names = API_KEY_NAMES
        secret_names = SECRET_KEY_NAMES

    api_key = _first_environment_value(
        source_environment,
        api_names,
    )
    secret_key = _first_environment_value(
        source_environment,
        secret_names,
    )

    missing: list[str] = []
    if not api_key:
        missing.append("/".join(api_names))
    if not secret_key:
        missing.append("/".join(secret_names))
    if missing:
        raise ConfigurationError(
            "缺少Alpaca paper凭据",
            code="ALPACA_CREDENTIALS_MISSING",
            details={"missing_variables": missing},
        )

    try:
        trading = trading_factory(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,
        )
        stock_data = stock_data_factory(
            api_key=api_key,
            secret_key=secret_key,
        )
    except Exception as error:
        raise BrokerUnavailableError(
            "无法创建Alpaca paper客户端",
            code="ALPACA_CLIENT_CREATION_FAILED",
            details={
                "exception_type": (
                    error.__class__.__name__
                )
            },
        ) from None

    clients = AlpacaClients(
        trading=trading,
        stock_data=stock_data,
        paper=True,
    )
    clients.validate()
    return clients


def call_api(
    operation: str,
    function: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    """Wrap an Alpaca SDK call without persisting its raw exception text."""

    try:
        return function(*args, **kwargs)
    except V2Error:
        raise
    except Exception as error:
        raise BrokerUnavailableError(
            f"Alpaca API暂时不可用：{operation}",
            code="ALPACA_API_UNAVAILABLE",
            details={
                "operation": operation,
                "exception_type": (
                    error.__class__.__name__
                ),
            },
        ) from None
