"""刷新 WA Trader v2 Stage F 的最终订单前事实快照。

作用：在 execution output 之后重新获取账户、可用持仓、挂单、当日订单、报价、资产能力和市场阶段。
重要性：订单数量与请求规格只能依据这份更晚的快照；关键刷新失败会全局阻止批准，绝不回退到旧行情。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TypeVar

from alpaca.data.enums import DataFeed

from v2.data._normalization import (
    compact_error,
    iso_timestamp,
    utc_now,
)
from v2.data.account import fetch_account
from v2.data.alpaca_client import AlpacaClients
from v2.data.assets import AssetCache
from v2.data.intraday import (
    determine_market_phase,
    fetch_market_holiday_status,
    market_data_feed,
)
from v2.data.orders import (
    fetch_open_orders,
    fetch_today_orders,
)
from v2.data.positions import fetch_positions
from v2.data.quotes import fetch_latest_quotes, no_quote
from v2.crypto_liquidation import (
    is_automatic_crypto_liquidation_decision,
)
from v2.exceptions import V2Error
from v2.models.orders import (
    PreTradeSnapshot,
    decimal_text,
    decimal_value,
)
from v2.profiles import OrderPolicy, RiskProfile
from v2.runtime import CyclePaths, atomic_write_json


T = TypeVar("T")


@dataclass(frozen=True)
class PreTradeSnapshotResult:
    snapshot: PreTradeSnapshot
    symbols: tuple[str, ...]

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.snapshot.payload

    @property
    def order_planning_ready(self) -> bool:
        return self.snapshot.order_planning_ready


def _safe_fetch(
    component: str,
    function: Callable[[], T],
    *,
    errors: list[dict[str, str]],
) -> T | None:
    try:
        return function()
    except V2Error as error:
        disposition = error.disposition()
        errors.append(
            compact_error(
                code=disposition.code,
                message=disposition.message,
                component=component,
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
                code=f"{component.upper()}_REFRESH_FAILED",
                message=f"{component}订单前刷新失败",
                component=component,
                exception_type=error.__class__.__name__,
            )
        )
    return None


def _strictly_after(
    requested: datetime,
    earlier: object,
) -> datetime:
    normalized = str(earlier or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return requested.astimezone(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    current = requested.astimezone(timezone.utc)
    return (
        parsed + timedelta(microseconds=1)
        if current <= parsed
        else current
    )


def _money(value: object) -> str:
    return decimal_text(decimal_value(value))


def _account_payload(
    account: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if account is None:
        return None
    result = dict(account)
    for field in (
        "cash",
        "buying_power",
        "portfolio_value",
        "equity",
        "long_market_value",
        "short_market_value",
    ):
        result[field] = _money(account.get(field))
    return result


def _position_payload(
    position: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(position)
    for field in (
        "quantity",
        "available_quantity",
        "average_entry_price",
        "market_value",
        "cost_basis",
        "unrealized_pl",
        "current_price",
        "lastday_price",
        "change_today",
    ):
        result[field] = _money(position.get(field))
    return result


def _order_payload(
    order: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(order)
    for field in (
        "quantity",
        "notional",
        "filled_quantity",
        "limit_price",
        "stop_price",
        "trail_price",
        "trail_percent",
        "high_water_mark",
    ):
        value = order.get(field)
        result[field] = (
            _money(value) if value is not None else None
        )
    quantity = (
        decimal_value(order.get("quantity"))
        if order.get("quantity") is not None
        else None
    )
    filled = decimal_value(
        order.get("filled_quantity", "0")
    )
    result["remaining_quantity"] = (
        decimal_text(max(quantity - filled, 0))
        if quantity is not None
        else None
    )
    raw_legs = order.get("legs")
    result["legs"] = [
        _order_payload(item)
        for item in (
            raw_legs
            if isinstance(raw_legs, list)
            else []
        )
        if isinstance(item, Mapping)
    ]
    return result


def _quote_payload(
    quote: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(quote)
    for field in (
        "bid_price",
        "bid_size",
        "ask_price",
        "ask_size",
        "midpoint",
        "spread",
        "spread_bps",
        "quote_age_seconds",
    ):
        value = quote.get(field)
        result[field] = (
            _money(value) if value is not None else None
        )
    return result


def _configured_equity_feed(
    market_phase: str,
    risk_profile: RiskProfile,
) -> DataFeed | None:
    configured = str(
        risk_profile.settings.get(
            "regular_equity_data_feed",
            "",
        )
    ).strip().lower()
    if (
        market_phase == "regular_session"
        and configured in {"iex", "sip"}
    ):
        return DataFeed(configured)
    return market_data_feed(market_phase)


def _limit_recheck_symbols(
    execution_output: Mapping[str, Any],
) -> set[str]:
    result: set[str] = set()
    decisions = execution_output.get("decisions", [])
    for item in (
        decisions
        if isinstance(decisions, list)
        else []
    ):
        if not isinstance(item, Mapping):
            continue
        intent = item.get("order_intent")
        if not isinstance(intent, Mapping):
            continue
        if (
            item.get("execution_decision")
            in {"approve", "modify"}
            and str(
                intent.get("preferred_type", "")
            ).strip().lower()
            == "limit"
        ):
            symbol = str(
                item.get("symbol", "")
            ).strip().upper()
            if symbol:
                result.add(symbol)
    return result


def _spread_passes(
    quote: Mapping[str, Any],
    *,
    spread_limit: Any,
    max_age: Any,
) -> bool:
    if quote.get("status") != "success":
        return False
    if quote.get("spread_bps") is None:
        return False
    if quote.get("quote_age_seconds") is None:
        return False
    return (
        decimal_value(quote["spread_bps"])
        <= decimal_value(spread_limit)
        and decimal_value(
            quote["quote_age_seconds"]
        )
        <= decimal_value(max_age)
    )


def _spread_sample(
    quote: Mapping[str, Any],
    *,
    sampled_at: datetime,
) -> dict[str, Any]:
    return {
        "sampled_at": iso_timestamp(sampled_at),
        "status": str(
            quote.get("status", "no_data")
        ),
        "data_feed": quote.get("data_feed"),
        "bid_exchange": quote.get(
            "bid_exchange"
        ),
        "ask_exchange": quote.get(
            "ask_exchange"
        ),
        "bid_price": (
            _money(quote.get("bid_price"))
            if quote.get("bid_price") is not None
            else None
        ),
        "ask_price": (
            _money(quote.get("ask_price"))
            if quote.get("ask_price") is not None
            else None
        ),
        "spread_bps": (
            _money(quote.get("spread_bps"))
            if quote.get("spread_bps")
            is not None
            else None
        ),
        "quote_timestamp": quote.get(
            "quote_timestamp"
        ),
        "quote_age_seconds": (
            _money(
                quote.get("quote_age_seconds")
            )
            if quote.get(
                "quote_age_seconds"
            )
            is not None
            else None
        ),
    }


def _recheck_wide_spreads(
    clients: AlpacaClients,
    *,
    quotes: dict[str, dict[str, Any]],
    execution_output: Mapping[str, Any],
    risk_profile: RiskProfile,
    market_phase: str,
    feed: DataFeed | None,
    sleep_func: Callable[[float], None],
    now_func: Callable[[], datetime],
    warnings: list[dict[str, str]],
) -> tuple[
    dict[str, dict[str, Any]],
    set[str],
]:
    settings = risk_profile.settings
    if (
        settings.get(
            "spread_recheck_enabled"
        )
        is not True
        or market_phase != "regular_session"
        or feed != DataFeed.IEX
    ):
        return {}, set()

    spread_limit = settings.get(
        "regular_spread_limit_bps"
    )
    max_age = settings.get(
        "quote_max_age_seconds"
    )
    window = float(
        settings.get(
            "spread_recheck_window_seconds",
            0,
        )
    )
    interval = float(
        settings.get(
            "spread_recheck_interval_seconds",
            0,
        )
    )
    required = int(
        settings.get(
            (
                "spread_recheck_required_"
                "consecutive_passes"
            ),
            0,
        )
    )
    if (
        spread_limit is None
        or max_age is None
        or window <= 0
        or interval <= 0
        or required < 2
    ):
        return {}, set()

    limit_symbols = _limit_recheck_symbols(
        execution_output
    )
    candidates = {
        symbol
        for symbol in limit_symbols
        if (
            symbol in quotes
            and quotes[symbol].get("status")
            == "success"
            and quotes[symbol].get(
                "spread_bps"
            )
            is not None
            and decimal_value(
                quotes[symbol]["spread_bps"]
            )
            > decimal_value(spread_limit)
        )
    }
    if not candidates:
        return {}, set()

    attempts = max(
        1,
        int(math.ceil(window / interval)),
    )
    metadata: dict[str, dict[str, Any]] = {
        symbol: {
            "attempted": True,
            "status": "failed",
            "data_feed": feed.value,
            "window_seconds": _money(window),
            "interval_seconds": _money(
                interval
            ),
            "required_consecutive_passes": (
                required
            ),
            "observed_consecutive_passes": 0,
            "sample_count": 1,
            "samples": [
                _spread_sample(
                    quotes[symbol],
                    sampled_at=now_func(),
                )
            ],
        }
        for symbol in sorted(candidates)
    }
    consecutive = {
        symbol: 0 for symbol in candidates
    }
    last_counted_timestamp: dict[
        str, str | None
    ] = {
        symbol: None for symbol in candidates
    }
    passed: set[str] = set()

    for _ in range(attempts):
        sleep_func(interval)
        sampled_at = now_func()
        refreshed = _safe_fetch(
            "quote_recheck",
            lambda: fetch_latest_quotes(
                clients,
                sorted(candidates),
                now=sampled_at,
                feed=feed,
            ),
            errors=warnings,
        )
        refreshed_quotes = (
            refreshed
            if isinstance(refreshed, dict)
            else {}
        )
        for symbol in sorted(candidates):
            quote = refreshed_quotes.get(
                symbol,
                no_quote(
                    symbol,
                    status="no_data",
                    data_feed=feed.value,
                ),
            )
            quotes[symbol] = quote
            item = metadata[symbol]
            item["samples"].append(
                _spread_sample(
                    quote,
                    sampled_at=sampled_at,
                )
            )
            item["sample_count"] = len(
                item["samples"]
            )
            timestamp = quote.get(
                "quote_timestamp"
            )
            if _spread_passes(
                quote,
                spread_limit=spread_limit,
                max_age=max_age,
            ):
                if (
                    timestamp
                    and timestamp
                    != last_counted_timestamp[
                        symbol
                    ]
                ):
                    consecutive[symbol] += 1
                    last_counted_timestamp[
                        symbol
                    ] = str(timestamp)
            else:
                consecutive[symbol] = 0
                last_counted_timestamp[
                    symbol
                ] = None
                passed.discard(symbol)
            item[
                "observed_consecutive_passes"
            ] = consecutive[symbol]
            if consecutive[symbol] >= required:
                item["status"] = (
                    "pending_final_validation"
                )
                passed.add(symbol)
        if passed == candidates:
            break

    failed = candidates - passed
    for symbol in failed:
        unstable = dict(quotes[symbol])
        unstable["status"] = "unstable_data"
        quotes[symbol] = unstable
        metadata[symbol]["status"] = "failed"
    return metadata, passed


def _execution_symbols(
    execution_output: Mapping[str, Any],
    positions: list[Mapping[str, Any]],
    open_orders: list[Mapping[str, Any]],
) -> tuple[str, ...]:
    decisions = execution_output.get("decisions", [])
    return tuple(
        sorted(
            {
                str(item.get("symbol", "")).strip().upper()
                for item in [
                    *(
                        decisions
                        if isinstance(decisions, list)
                        else []
                    ),
                    *positions,
                    *open_orders,
                ]
                if isinstance(item, Mapping)
                and str(item.get("symbol", "")).strip()
            }
        )
    )


def create_pretrade_snapshot(
    paths: CyclePaths,
    clients: AlpacaClients,
    *,
    execution_output: Mapping[str, Any],
    order_policy: OrderPolicy,
    risk_profile: RiskProfile,
    now: datetime | None = None,
    is_market_holiday: bool | None = None,
    asset_cache: AssetCache | None = None,
    sleep_func: Callable[[float], None] = (
        time.sleep
    ),
    now_func: Callable[[], datetime] = utc_now,
) -> PreTradeSnapshotResult:
    """Refresh, decimalize and atomically persist order-planning facts."""

    clients.validate()
    requested = now or utc_now()
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    retrieved = _strictly_after(
        requested,
        execution_output.get("generated_at"),
    )
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    account = _safe_fetch(
        "account",
        lambda: fetch_account(
            clients,
            retrieved_at=retrieved,
        ),
        errors=errors,
    )
    positions = _safe_fetch(
        "positions",
        lambda: fetch_positions(clients),
        errors=errors,
    )
    open_orders = _safe_fetch(
        "open_orders",
        lambda: fetch_open_orders(clients),
        errors=errors,
    )
    today_orders = _safe_fetch(
        "today_orders",
        lambda: fetch_today_orders(
            clients,
            now=retrieved,
        ),
        errors=errors,
    )
    normalized_positions = (
        positions if isinstance(positions, list) else []
    )
    normalized_open_orders = (
        open_orders if isinstance(open_orders, list) else []
    )
    normalized_today_orders = (
        today_orders if isinstance(today_orders, list) else []
    )
    symbols = _execution_symbols(
        execution_output,
        normalized_positions,
        normalized_open_orders,
    )
    executable_symbols = {
        str(item.get("symbol", "")).strip().upper()
        for item in execution_output.get(
            "decisions",
            [],
        )
        if isinstance(item, Mapping)
        and item.get("execution_decision")
        in {"approve", "modify"}
    }

    holiday = is_market_holiday
    if holiday is None:
        holiday = _safe_fetch(
            "market_calendar",
            lambda: fetch_market_holiday_status(
                clients,
                retrieved,
            ),
            errors=warnings,
        )
    market_phase = (
        determine_market_phase(
            retrieved,
            is_market_holiday=bool(holiday),
        )
        if holiday is not None
        else "unknown"
    )
    feed = _configured_equity_feed(
        market_phase,
        risk_profile,
    )
    active_asset_cache = (
        asset_cache or AssetCache(clients)
    )
    asset_result = active_asset_cache.get_many(
        list(symbols)
    )
    warnings.extend(asset_result.errors)
    crypto_symbols = [
        symbol
        for symbol in symbols
        if asset_result.assets.get(
            symbol, {}
        ).get("asset_class")
        == "crypto"
    ]
    quotes = _safe_fetch(
        "quotes",
        lambda: fetch_latest_quotes(
            clients,
            list(symbols),
            now=retrieved,
            feed=feed,
            crypto_symbols=crypto_symbols,
        ),
        errors=errors,
    )
    normalized_quotes = (
        quotes if isinstance(quotes, dict) else {}
    )
    for symbol in symbols:
        normalized_quotes.setdefault(
            symbol,
            no_quote(
                symbol,
                data_feed=(
                    feed.value
                    if feed is not None
                    else "subscription_default"
                ),
            ),
        )

    spread_rechecks, stabilized_symbols = (
        _recheck_wide_spreads(
            clients,
            quotes=normalized_quotes,
            execution_output=execution_output,
            risk_profile=risk_profile,
            market_phase=market_phase,
            feed=feed,
            sleep_func=sleep_func,
            now_func=now_func,
            warnings=warnings,
        )
    )
    if stabilized_symbols:
        retrieved = _strictly_after(
            now_func(),
            retrieved,
        )
        account = _safe_fetch(
            "account",
            lambda: fetch_account(
                clients,
                retrieved_at=retrieved,
            ),
            errors=errors,
        )
        positions = _safe_fetch(
            "positions",
            lambda: fetch_positions(clients),
            errors=errors,
        )
        open_orders = _safe_fetch(
            "open_orders",
            lambda: fetch_open_orders(clients),
            errors=errors,
        )
        today_orders = _safe_fetch(
            "today_orders",
            lambda: fetch_today_orders(
                clients,
                now=retrieved,
            ),
            errors=errors,
        )
        normalized_positions = (
            positions
            if isinstance(positions, list)
            else []
        )
        normalized_open_orders = (
            open_orders
            if isinstance(open_orders, list)
            else []
        )
        normalized_today_orders = (
            today_orders
            if isinstance(today_orders, list)
            else []
        )
        symbols = _execution_symbols(
            execution_output,
            normalized_positions,
            normalized_open_orders,
        )
        asset_result = (
            active_asset_cache.get_many(
                list(symbols)
            )
        )
        warnings.extend(asset_result.errors)
        crypto_symbols = [
            symbol
            for symbol in symbols
            if asset_result.assets.get(
                symbol,
                {},
            ).get("asset_class")
            == "crypto"
        ]
        final_quotes = _safe_fetch(
            "quotes",
            lambda: fetch_latest_quotes(
                clients,
                list(symbols),
                now=retrieved,
                feed=feed,
                crypto_symbols=crypto_symbols,
            ),
            errors=errors,
        )
        normalized_quotes = (
            final_quotes
            if isinstance(final_quotes, dict)
            else {}
        )
        for symbol in symbols:
            normalized_quotes.setdefault(
                symbol,
                no_quote(
                    symbol,
                    data_feed=(
                        feed.value
                        if feed is not None
                        else (
                            "subscription_default"
                        )
                    ),
                ),
            )
        spread_limit = risk_profile.settings.get(
            "regular_spread_limit_bps"
        )
        max_age = risk_profile.settings.get(
            "quote_max_age_seconds"
        )
        for symbol in stabilized_symbols:
            quote = normalized_quotes.get(
                symbol,
                no_quote(
                    symbol,
                    data_feed=(
                        feed.value
                        if feed is not None
                        else (
                            "subscription_default"
                        )
                    ),
                ),
            )
            item = spread_rechecks[symbol]
            item["final_sample"] = (
                _spread_sample(
                    quote,
                    sampled_at=retrieved,
                )
            )
            final_passed = (
                spread_limit is not None
                and max_age is not None
                and _spread_passes(
                    quote,
                    spread_limit=spread_limit,
                    max_age=max_age,
                )
            )
            item["status"] = (
                "passed"
                if final_passed
                else "failed_final_validation"
            )
            if not final_passed:
                unstable = dict(quote)
                unstable["status"] = (
                    "unstable_data"
                )
                normalized_quotes[
                    symbol
                ] = unstable

    for symbol, metadata in spread_rechecks.items():
        quote = dict(
            normalized_quotes.get(
                symbol,
                no_quote(symbol),
            )
        )
        quote["spread_recheck"] = metadata
        normalized_quotes[symbol] = quote

    crypto_policy = order_policy.settings.get(
        "crypto"
    )
    crypto_policy = (
        crypto_policy
        if isinstance(crypto_policy, Mapping)
        else {}
    )
    automatic_crypto_liquidations = {
        str(item.get("symbol", "")).strip().upper()
        for item in execution_output.get("decisions", [])
        if isinstance(item, Mapping)
        and is_automatic_crypto_liquidation_decision(
            item,
            asset_result.assets.get(
                str(
                    item.get("symbol", "")
                ).strip().upper(),
                {},
            ),
            crypto_policy,
        )
    }
    required_quote_symbols = (
        executable_symbols
        - automatic_crypto_liquidations
    )
    missing_quotes = sorted(
        symbol
        for symbol in required_quote_symbols
        if normalized_quotes.get(symbol, {}).get(
            "status"
        )
        != "success"
    )
    missing_assets = sorted(
        executable_symbols - set(asset_result.assets)
    )
    if missing_quotes:
        errors.append(
            {
                "code": "PRETRADE_QUOTES_INCOMPLETE",
                "message": "可执行标的缺少有效最新报价",
                "component": "quotes",
                "symbols": ",".join(missing_quotes),
            }
        )
    if missing_assets:
        errors.append(
            {
                "code": "PRETRADE_ASSETS_INCOMPLETE",
                "message": "可执行标的缺少资产能力",
                "component": "assets",
                "symbols": ",".join(missing_assets),
            }
        )

    critical_components = {
        "account",
        "positions",
        "open_orders",
        "today_orders",
        "quotes",
        "assets",
    }
    critical_errors = [
        item
        for item in errors
        if (
            item.get("component")
            in critical_components
            and not (
                item.get("component") == "quotes"
                and not required_quote_symbols
            )
        )
    ]
    order_planning_ready = not critical_errors
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "pretrade_snapshot",
        "profile_id": paths.profile_id,
        "strategy_id": paths.strategy_id,
        "strategy_version": paths.strategy_version,
        "run_date": paths.run_date,
        "cycle_id": paths.cycle_id,
        "execution_generated_at": (
            execution_output.get("generated_at")
        ),
        "retrieved_at": iso_timestamp(retrieved),
        "market_phase": market_phase,
        "account": _account_payload(
            account
            if isinstance(account, Mapping)
            else None
        ),
        "positions": [
            _position_payload(item)
            for item in normalized_positions
        ],
        "open_orders": [
            _order_payload(item)
            for item in normalized_open_orders
        ],
        "today_orders": [
            _order_payload(item)
            for item in normalized_today_orders
        ],
        "quotes": {
            symbol: _quote_payload(quote)
            for symbol, quote in sorted(
                normalized_quotes.items()
            )
        },
        "assets": {
            symbol: dict(asset)
            for symbol, asset in sorted(
                asset_result.assets.items()
            )
        },
        "broker_capabilities": dict(
            order_policy.settings.get(
                "broker_capabilities",
                {},
            )
        ),
        "order_policy": order_policy.reference,
        "order_planning_ready": order_planning_ready,
        "data_quality": {
            "critical_error_count": len(
                critical_errors
            ),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
        },
    }
    atomic_write_json(
        paths.pretrade_snapshot,
        payload,
    )
    return PreTradeSnapshotResult(
        snapshot=PreTradeSnapshot.from_payload(
            payload
        ),
        symbols=symbols,
    )
