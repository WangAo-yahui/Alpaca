"""提供 v2 单元测试使用的无网络 Alpaca 对象。

作用：模拟账户、持仓、订单、资产、报价、成交和分钟线读取。
重要性：验证 Stage E 时绝不能因测试而触发真实 broker 写操作。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def fake_account(
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "account-123",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
        "cash": "10000.50",
        "buying_power": "18000.25",
        "portfolio_value": "25000.00",
        "equity": "25000.00",
        "long_market_value": "14999.50",
        "short_market_value": "0",
        "currency": "USD",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_position(
    symbol: str = "MU",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "symbol": symbol,
        "asset_id": f"asset-{symbol}",
        "side": "long",
        "qty": "10",
        "qty_available": "8",
        "avg_entry_price": "100",
        "market_value": "1100",
        "cost_basis": "1000",
        "unrealized_pl": "100",
        "current_price": "110",
        "lastday_price": "108",
        "change_today": "0.0185",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_order(
    symbol: str = "MU",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": f"order-{symbol}",
        "client_order_id": f"wa2-{symbol}-buy-0",
        "symbol": symbol,
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "qty": "5",
        "filled_qty": "1",
        "limit_price": "105",
        "stop_price": None,
        "status": "new",
        "extended_hours": True,
        "submitted_at": (
            "2026-07-23T13:00:00+00:00"
        ),
        "updated_at": (
            "2026-07-23T13:01:00+00:00"
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_asset(
    symbol: str = "MU",
    **overrides: Any,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "symbol": symbol,
        "tradable": True,
        "fractionable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "exchange": "NASDAQ",
        "asset_class": "us_equity",
        "status": "active",
        "attributes": [],
        "overnight_tradable": False,
        "overnight_halted": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeTradingClient:
    def __init__(
        self,
        *,
        account: object | None = None,
        positions: list[object] | None = None,
        open_orders: list[object] | None = None,
        today_orders: list[object] | None = None,
        assets: dict[str, object] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.account = (
            account
            if account is not None
            else fake_account()
        )
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.today_orders = today_orders or []
        self.assets = assets or {}
        self.failures = failures or set()
        self.asset_calls: list[str] = []

    def _fail(self, operation: str) -> None:
        if operation in self.failures:
            raise RuntimeError(
                f"fake failure: {operation}"
            )

    def get_account(self) -> object:
        self._fail("account")
        return self.account

    def get_all_positions(self) -> list[object]:
        self._fail("positions")
        return self.positions

    def get_orders(
        self,
        *,
        filter: object,
    ) -> list[object]:
        status = getattr(
            getattr(filter, "status", None),
            "value",
            getattr(filter, "status", None),
        )
        if status == "open":
            self._fail("open_orders")
            return self.open_orders
        self._fail("today_orders")
        return self.today_orders

    def get_asset(self, symbol: str) -> object:
        self.asset_calls.append(symbol)
        if (
            "assets" in self.failures
            or f"asset:{symbol}" in self.failures
        ):
            raise RuntimeError(
                f"fake asset failure: {symbol}"
            )
        return self.assets.get(
            symbol,
            fake_asset(symbol),
        )

    def get_calendar(
        self,
        request: object,
    ) -> list[object]:
        self._fail("calendar")
        return [SimpleNamespace()]


class FakeStockDataClient:
    def __init__(
        self,
        *,
        quotes: dict[str, object] | None = None,
        trades: dict[str, object] | None = None,
        bars: dict[str, list[object]] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.quotes = quotes or {}
        self.trades = trades or {}
        self.bars = bars or {}
        self.failures = failures or set()

    def get_stock_latest_quote(
        self,
        request: object,
    ) -> dict[str, object]:
        if "quotes" in self.failures:
            raise RuntimeError("fake quote failure")
        return self.quotes

    def get_stock_bars(
        self,
        request: object,
    ) -> dict[str, list[object]]:
        if "bars" in self.failures:
            raise RuntimeError("fake bars failure")
        return self.bars

    def get_stock_latest_trade(
        self,
        request: object,
    ) -> dict[str, object]:
        if "trades" in self.failures:
            raise RuntimeError("fake trade failure")
        return self.trades
