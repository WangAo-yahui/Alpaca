"""构造 Stage F 订单规划测试使用的确定性 Decimal 事实。

作用：集中提供 execution、portfolio、pre-trade、风险、订单策略与伪状态。
重要性：每个安全测试只改变一个事实，避免 fixture 漂移掩盖资金重复、超卖或市场状态错误。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from v2.models.orders import PreTradeSnapshot
from v2.profiles import (
    load_order_policy,
    load_risk_profile,
)
from v2.runtime import build_cycle_paths


ACCOUNT_HASH = "a" * 64
GENERATED_AT = datetime(
    2026,
    7,
    24,
    14,
    0,
    tzinfo=timezone.utc,
)


def order_paths(root: Path):
    return build_cycle_paths(
        cycle_id="20260724T140000",
        run_date="2026-07-24",
        project_root=root,
        profile_id="paper1",
        strategy_id="core_long",
        strategy_version="1.2.0",
    )


def order_state(*, allow_trade: bool = False):
    return SimpleNamespace(
        trade_permission=SimpleNamespace(
            submission_enabled=allow_trade
        )
    )


def execution_decision(
    symbol: str = "MU",
    **overrides: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "symbol": symbol,
        "portfolio_action": "open",
        "execution_decision": "approve",
        "side": "buy",
        "target_weight": "0.08",
        "maximum_weight": "0.08",
        "execution_fraction": "0.50",
        "urgency": "normal",
        "price_condition": {
            "reference": "ask",
            "limit_price": "100.10",
            "do_not_execute_above": "101.00",
            "review_below": None,
        },
        "order_intent": {
            "preferred_type": "limit",
            "time_in_force_preference": "day",
            "extended_hours_requested": False,
            "allow_queue": False,
            "allow_partial_fill": True,
        },
    }
    result.update(overrides)
    return result


def execution_output(
    *decisions: dict[str, Any],
    actions: list[dict[str, Any]] | None = None,
    protection_plans: list[
        dict[str, Any]
    ] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "stage": "execution_decision",
        "profile_id": "paper1",
        "strategy_id": "core_long",
        "strategy_version": "1.2.0",
        "run_date": "2026-07-24",
        "cycle_id": "20260724T140000",
        "generated_at": GENERATED_AT.isoformat(),
        "decisions": list(
            decisions or (execution_decision(),)
        ),
        "protection_plans": protection_plans or [],
        "open_order_actions": actions or [],
        "requires_manual_review": False,
    }


def portfolio_output(
    *symbols: str,
) -> dict[str, Any]:
    selected = symbols or ("MU",)
    return {
        "decisions": [
            {
                "symbol": symbol,
                "priority": index,
                "conviction": "medium",
                "sector": (
                    "Technology"
                    if symbol in {"MU", "AMD"}
                    else "Consumer"
                ),
            }
            for index, symbol in enumerate(
                selected,
                start=1,
            )
        ]
    }


def pretrade_payload(
    *,
    symbols: tuple[str, ...] = ("MU",),
    market_phase: str = "regular_session",
    positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    today_orders: list[dict[str, Any]] | None = None,
    ready: bool = True,
    cash: str = "50000",
    buying_power: str = "50000",
    portfolio_value: str = "100000",
    quote_age: str = "1",
    spread_bps: str = "10",
    tradable: bool = True,
    fractionable: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "stage": "pretrade_snapshot",
        "profile_id": "paper1",
        "strategy_id": "core_long",
        "strategy_version": "1.2.0",
        "run_date": "2026-07-24",
        "cycle_id": "20260724T140000",
        "execution_generated_at": (
            GENERATED_AT.isoformat()
        ),
        "retrieved_at": (
            GENERATED_AT
            + timedelta(seconds=1)
        ).isoformat(),
        "market_phase": market_phase,
        "account": {
            "account_id_hash": ACCOUNT_HASH,
            "status": "ACTIVE",
            "trading_blocked": False,
            "account_blocked": False,
            "trade_suspended_by_user": False,
            "cash": cash,
            "buying_power": buying_power,
            "portfolio_value": portfolio_value,
            "equity": portfolio_value,
            "long_market_value": "0",
            "short_market_value": "0",
        },
        "positions": positions or [],
        "open_orders": open_orders or [],
        "today_orders": today_orders or [],
        "quotes": {
            symbol: {
                "symbol": symbol,
                "status": "success",
                "bid_price": "100.00",
                "bid_size": "10",
                "ask_price": "100.10",
                "ask_size": "10",
                "midpoint": "100.05",
                "spread": "0.10",
                "spread_bps": spread_bps,
                "quote_timestamp": (
                    GENERATED_AT.isoformat()
                ),
                "quote_age_seconds": quote_age,
            }
            for symbol in symbols
        },
        "assets": {
            symbol: {
                "symbol": symbol,
                "tradable": tradable,
                "fractionable": fractionable,
                "shortable": False,
                "easy_to_borrow": False,
                "exchange": "NASDAQ",
                "asset_class": "us_equity",
                "status": "active",
            }
            for symbol in symbols
        },
        "broker_capabilities": {
            "supports_fractional_market_day": True,
            "supports_fractional_limit_day": True,
            "supports_extended_hours": True,
            "supports_closed_session_queue": False,
        },
        "order_policy": "paper_equity@1.0.0",
        "order_planning_ready": ready,
        "data_quality": {
            "critical_error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
        },
    }


def order_configs():
    root = Path(__file__).resolve().parents[2]
    return (
        load_risk_profile(
            "paper_standard@1.1.0",
            project_root=root,
        ),
        load_order_policy(
            "paper_equity@1.0.0",
            project_root=root,
        ),
    )


def snapshot(**kwargs: Any) -> PreTradeSnapshot:
    return PreTradeSnapshot.from_payload(
        pretrade_payload(**kwargs)
    )
