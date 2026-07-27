"""安全创建和调用 WA Trader v2 的 Alpaca Paper/Live 客户端。

作用：按 profile 指定的环境变量名读取凭据，并统一包装 broker API 异常。
重要性：该模块不得泄露密钥，且必须确保 profile、凭据文件与 SDK 环境一致，是 broker 访问的核心安全边界。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from typing import TYPE_CHECKING

from alpaca.data.historical import (
    CryptoHistoricalDataClient,
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
    crypto_data: Any | None = None

    @property
    def environment(self) -> str:
        return "paper" if self.paper else "live"

    @property
    def live(self) -> bool:
        return not self.paper

    def validate(self) -> None:
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
    crypto_data_factory: Callable[
        ..., Any
    ] = CryptoHistoricalDataClient,
    profile: "Profile | None" = None,
) -> AlpacaClients:
    """Create the only Alpaca client pair used by v2.

    Supplying ``environ`` disables dotenv loading, which keeps tests hermetic.
    Neither credential value is ever included in an error message or details.
    """

    if paper == live:
        raise ConfigurationError(
            "Alpaca客户端必须且只能选择一个环境",
            code="ALPACA_ENVIRONMENT_INVALID",
        )
    if live and profile is None:
        raise LiveTradingRejected()

    if environ is None:
        root = (
            project_root.expanduser().resolve()
            if project_root is not None
            else get_project_root()
        )
        dotenv_override = os.environ.get(
            "WA_DOTENV_PATH", ""
        ).strip()
        override_path = (
            Path(dotenv_override).expanduser()
            if dotenv_override
            else None
        )
        if (
            override_path is not None
            and not override_path.is_absolute()
        ):
            raise ConfigurationError(
                "WA_DOTENV_PATH必须是绝对路径",
                code="DOTENV_PATH_INVALID",
            )
        dotenv_path = (
            override_path.resolve()
            if override_path is not None
            else root
            / (
                ".env_live"
                if (
                    profile is not None
                    and profile.environment == "live"
                )
                else ".env"
            )
        )
        load_dotenv(
            dotenv_path=dotenv_path,
            override=False,
        )
        source_environment: Mapping[str, str] = (
            os.environ
        )
    else:
        source_environment = environ

    if profile is not None:
        expected_paper = (
            profile.environment == "paper"
        )
        if paper != expected_paper:
            raise ConfigurationError(
                "CLI环境与profile环境不一致",
                code="ALPACA_ENVIRONMENT_MISMATCH",
            )
        api_names = (
            profile.credential_key_env,
        )
        secret_names = (
            profile.credential_secret_env,
        )
    else:
        if paper and not _paper_environment_enabled(
            source_environment
        ):
            raise ConfigurationError(
                "ALPACA_PAPER与请求环境不一致",
                code="ALPACA_ENVIRONMENT_MISMATCH",
            )
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
            "缺少当前profile的Alpaca凭据",
            code="ALPACA_CREDENTIALS_MISSING",
            details={"missing_variables": missing},
        )

    try:
        trading = trading_factory(
            api_key=api_key,
            secret_key=secret_key,
            paper=paper,
        )
        stock_data = stock_data_factory(
            api_key=api_key,
            secret_key=secret_key,
        )
        crypto_data = crypto_data_factory(
            api_key=api_key,
            secret_key=secret_key,
        )
    except Exception as error:
        raise BrokerUnavailableError(
            "无法创建Alpaca客户端",
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
        paper=paper,
        crypto_data=crypto_data,
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
