"""把 Stage E 执行意图确定性地转换为 Stage F 拟定订单。

作用：计算当前与挂单潜在暴露、目标差额、执行比例、资本顺序、最终数量、价格和依赖动作。
重要性：所有计算使用 Decimal 并向下量化，避免重复占用资金、超卖、做空或因浮点误差超买。
"""

from __future__ import annotations

from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
)
from typing import Any, Mapping

from v2.crypto_liquidation import (
    is_automatic_crypto_liquidation_decision,
)
from v2.data.orders import (
    is_system_protective_order,
)
from v2.models.orders import (
    ZERO,
    OrderAction,
    OrderStatus,
    PreTradeSnapshot,
    ProposedOrder,
    ProposedOrderAction,
    ProposedOrderPlan,
    SubmissionPermission,
    canonical_hash,
    decimal_or_zero,
    decimal_text,
    decimal_value,
)
from v2.models.state import CycleState
from v2.profiles import OrderPolicy, RiskProfile
from v2.runtime import CyclePaths, utc_now_iso
from v2.trading.idempotency import (
    build_client_order_id,
    build_plan_id,
)
from v2.trading.protection import (
    apply_protection_plans,
)


URGENCY_RANK = {
    "high": 0,
    "normal": 1,
    "low": 2,
    "none": 3,
}
CONVICTION_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "none": 3,
}
EXECUTABLE_DECISIONS = {"approve", "modify"}
ACTIVE_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "held",
    "pending_replace",
    "accepted_for_bidding",
}
EXTENDED_PHASES = {
    "overnight",
    "overnight_session",
    "before_market_open",
    "after_market_close",
}
CLOSED_PHASES = {
    "market_closed_weekend",
    "market_closed_holiday",
}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        item for item in value
        if isinstance(item, Mapping)
    ]


def _quantum(precision: int) -> Decimal:
    return Decimal("1").scaleb(-max(0, precision))


def _floor(
    value: Decimal,
    precision: int,
) -> Decimal:
    return value.quantize(
        _quantum(precision),
        rounding=ROUND_DOWN,
    )


def _floor_to_increment(
    value: Decimal,
    increment: Decimal,
) -> Decimal:
    if increment <= ZERO:
        return value
    units = (value / increment).to_integral_value(
        rounding=ROUND_FLOOR,
    )
    return units * increment


def _ceil_to_increment(
    value: Decimal,
    increment: Decimal,
) -> Decimal:
    if increment <= ZERO:
        return value
    units = (value / increment).to_integral_value(
        rounding=ROUND_CEILING,
    )
    return units * increment


def _active_orders(
    snapshot: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for order in _records(
        snapshot.get("open_orders")
    ):
        status = str(order.get("status", "")).lower()
        if status and status not in ACTIVE_ORDER_STATUSES:
            continue
        identity = (
            str(order.get("broker_order_id") or "")
            or str(order.get("client_order_id") or "")
            or "|".join(
                (
                    str(order.get("symbol", "")),
                    str(order.get("side", "")),
                    str(order.get("submitted_at", "")),
                )
            )
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(order)
    return result


def _remaining_quantity(
    order: Mapping[str, Any],
) -> Decimal | None:
    if order.get("remaining_quantity") is not None:
        remaining = decimal_or_zero(
            order.get("remaining_quantity")
        )
        return max(remaining, ZERO)
    if order.get("quantity") is None:
        return None
    quantity = decimal_or_zero(order.get("quantity"))
    filled = decimal_or_zero(
        order.get("filled_quantity")
    )
    return max(quantity - filled, ZERO)


def _quote_reference(
    quote: Mapping[str, Any],
    reference: str,
) -> Decimal | None:
    field = {
        "bid": "bid_price",
        "ask": "ask_price",
        "midpoint": "midpoint",
    }.get(reference)
    if field is None:
        return None
    value = quote.get(field)
    if value is None:
        return None
    result = decimal_or_zero(value)
    return result if result > ZERO else None


def _order_reference_price(
    order: Mapping[str, Any],
    quote: Mapping[str, Any],
) -> Decimal | None:
    limit = order.get("limit_price")
    if limit is not None:
        value = decimal_or_zero(limit)
        if value > ZERO:
            return value
    side = str(order.get("side", "")).lower()
    return _quote_reference(
        quote,
        "ask" if side == "buy" else "bid",
    ) or _quote_reference(quote, "midpoint")


def _positions_by_symbol(
    snapshot: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("symbol", "")).upper(): item
        for item in _records(snapshot.get("positions"))
        if item.get("symbol")
    }


def _portfolio_metadata(
    portfolio_output: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("symbol", "")).upper(): item
        for item in _records(
            portfolio_output.get("decisions")
        )
        if item.get("symbol")
    }


def _decision_sort_key(
    indexed: tuple[int, Mapping[str, Any]],
    portfolio: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int, int, str, int]:
    index, decision = indexed
    symbol = str(
        decision.get("symbol", "")
    ).upper()
    metadata = _mapping(portfolio.get(symbol))
    try:
        priority = int(metadata.get("priority", 999999))
    except (TypeError, ValueError):
        priority = 999999
    conviction = str(
        metadata.get("conviction", "none")
    ).lower()
    urgency = str(
        decision.get("urgency", "none")
    ).lower()
    return (
        URGENCY_RANK.get(urgency, 9),
        priority,
        CONVICTION_RANK.get(conviction, 9),
        symbol,
        index,
    )


def _action_plan(
    *,
    paths: CyclePaths,
    execution_output: Mapping[str, Any],
    open_orders: list[Mapping[str, Any]],
    order_policy: OrderPolicy,
    permission: SubmissionPermission,
) -> tuple[
    tuple[ProposedOrderAction, ...],
    dict[str, tuple[str, ...]],
]:
    by_reference: dict[str, Mapping[str, Any]] = {}
    for order in open_orders:
        for field in (
            "broker_order_id",
            "client_order_id",
        ):
            value = str(order.get(field) or "")
            if value:
                by_reference[value] = order
    actions: list[ProposedOrderAction] = []
    dependencies: dict[str, list[str]] = {}
    raw_actions = _records(
        execution_output.get("open_order_actions")
    )
    for index, raw in enumerate(raw_actions):
        reference = str(
            raw.get("order_reference", "")
        )
        symbol = str(
            raw.get("symbol", "")
        ).upper()
        requested = str(
            raw.get("action", "review")
        ).lower()
        matched = by_reference.get(reference)
        action = (
            OrderAction(requested)
            if requested
            in {
                "keep",
                "cancel",
                "replace",
                "review",
            }
            else OrderAction.REVIEW
        )
        action_id = build_plan_id(
            profile_id=paths.profile_id,
            strategy_id=paths.strategy_id,
            strategy_version=paths.strategy_version,
            cycle_id=paths.cycle_id,
            symbol=symbol or "UNKNOWN",
            side=str(
                matched.get("side", "none")
                if matched is not None
                else "none"
            ),
            intent_index=index,
            order_role=f"action-{action.value}",
            idempotency_version=str(
                order_policy.settings.get(
                    "idempotency_version",
                    "1",
                )
            ),
        )
        if matched is None:
            status = OrderStatus.BLOCKED
            reason = "open_order_reference_not_found"
        elif action == OrderAction.KEEP:
            status = (
                OrderStatus.APPROVED
                if permission.submission_requested
                else OrderStatus.DRY_RUN_APPROVED
            )
            reason = str(
                raw.get("reason", "keep_existing_order")
            )
        elif action in {
            OrderAction.CANCEL,
            OrderAction.REPLACE,
        }:
            status = OrderStatus.DEPENDENT
            reason = (
                "stage_g_cancel_and_refresh_required"
            )
            dependencies.setdefault(
                symbol,
                [],
            ).append(action_id)
        else:
            status = OrderStatus.BLOCKED
            reason = "manual_review_required"
        actions.append(
            ProposedOrderAction(
                action_id=action_id,
                order_reference=reference,
                symbol=symbol,
                action=action,
                status=status,
                reason=reason,
                broker_order_id=(
                    str(
                        matched.get("broker_order_id")
                        or ""
                    )
                    or None
                    if matched is not None
                    else None
                ),
                client_order_id=(
                    str(
                        matched.get("client_order_id")
                        or ""
                    )
                    or None
                    if matched is not None
                    else None
                ),
            )
        )
    return (
        tuple(actions),
        {
            symbol: tuple(values)
            for symbol, values in dependencies.items()
        },
    )


def _exposure(
    *,
    symbol: str,
    position: Mapping[str, Any],
    open_orders: list[Mapping[str, Any]],
    quote: Mapping[str, Any],
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    bool,
]:
    current = abs(
        decimal_or_zero(position.get("market_value"))
    )
    available = max(
        decimal_or_zero(
            position.get("available_quantity")
        ),
        ZERO,
    )
    open_buy = ZERO
    open_sell = ZERO
    open_sell_qty = ZERO
    complete = True
    for order in open_orders:
        if (
            str(order.get("symbol", "")).upper()
            != symbol
        ):
            continue
        if is_system_protective_order(order):
            continue
        remaining = _remaining_quantity(order)
        if remaining is None or remaining <= ZERO:
            continue
        price = _order_reference_price(order, quote)
        if price is None:
            complete = False
            continue
        side = str(order.get("side", "")).lower()
        if side == "buy":
            open_buy += remaining * price
        elif side == "sell":
            usable = min(
                remaining,
                max(available - open_sell_qty, ZERO),
            )
            open_sell += usable * price
            open_sell_qty += usable
    potential = current + open_buy - open_sell
    return (
        current,
        open_buy,
        open_sell,
        potential,
        open_sell_qty,
        complete,
    )


def _empty_order(
    *,
    paths: CyclePaths,
    decision: Mapping[str, Any],
    intent_index: int,
    order_policy: OrderPolicy,
    status: OrderStatus,
    reason: str,
    market_phase: str,
) -> ProposedOrder:
    symbol = str(decision.get("symbol", "")).upper()
    side = str(decision.get("side", "none")).lower()
    version = str(
        order_policy.settings.get(
            "idempotency_version",
            "1",
        )
    )
    plan_id = build_plan_id(
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
        cycle_id=paths.cycle_id,
        symbol=symbol or "UNKNOWN",
        side=side,
        intent_index=intent_index,
        order_role="primary",
        idempotency_version=version,
    )
    client_id = build_client_order_id(
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
        cycle_id=paths.cycle_id,
        symbol=symbol or "UNKNOWN",
        side=side,
        intent_index=intent_index,
        order_role="primary",
        idempotency_version=version,
        max_length=int(
            order_policy.settings.get(
                "client_order_id_max_length",
                48,
            )
        ),
    )
    return ProposedOrder(
        plan_id=plan_id,
        symbol=symbol,
        side=side,
        order_type=str(
            _mapping(
                decision.get("order_intent")
            ).get("preferred_type", "none")
        ),
        quantity=ZERO,
        planned_value=ZERO,
        reference_price=ZERO,
        limit_price=None,
        time_in_force=str(
            _mapping(
                decision.get("order_intent")
            ).get("time_in_force_preference", "none")
        ),
        extended_hours=False,
        client_order_id=client_id,
        status=status,
        reason_codes=(reason,),
        current_position_value=ZERO,
        open_buy_remaining_value=ZERO,
        open_sell_remaining_value=ZERO,
        potential_position_value=ZERO,
        target_position_value=ZERO,
        raw_delta_value=ZERO,
        execution_delta_value=ZERO,
        market_phase=market_phase,
        urgency=str(decision.get("urgency", "none")),
    )


def build_order_plan(
    *,
    paths: CyclePaths,
    state: CycleState,
    execution_output: Mapping[str, Any],
    pretrade_snapshot: PreTradeSnapshot | Mapping[str, Any],
    portfolio_output: Mapping[str, Any],
    risk_profile: RiskProfile,
    order_policy: OrderPolicy,
    generated_at: str | None = None,
) -> ProposedOrderPlan:
    """Build one deterministic, capital-aware order plan without broker writes."""

    snapshot_model = (
        pretrade_snapshot
        if isinstance(
            pretrade_snapshot,
            PreTradeSnapshot,
        )
        else PreTradeSnapshot.from_payload(
            pretrade_snapshot
        )
    )
    snapshot = snapshot_model.payload
    permission = SubmissionPermission(
        submission_requested=(
            state.trade_permission.submission_enabled
        ),
        dry_run=not (
            state.trade_permission.submission_enabled
        ),
    )
    open_orders = _active_orders(snapshot)
    actions, action_dependencies = _action_plan(
        paths=paths,
        execution_output=execution_output,
        open_orders=open_orders,
        order_policy=order_policy,
        permission=permission,
    )
    global_issues: list[str] = []
    if not snapshot_model.order_planning_ready:
        global_issues.append(
            "pretrade_snapshot_not_ready"
        )

    account = _mapping(snapshot.get("account"))
    portfolio_value = decimal_or_zero(
        account.get("portfolio_value")
    )
    cash = decimal_or_zero(account.get("cash"))
    buying_power = decimal_or_zero(
        account.get("buying_power")
    )
    settings = risk_profile.settings
    minimum_cash = portfolio_value * decimal_or_zero(
        settings.get("minimum_cash_weight")
    )
    allocatable = max(cash - minimum_cash, ZERO)
    per_cycle = portfolio_value * decimal_or_zero(
        settings.get(
            "maximum_new_capital_per_cycle_weight"
        )
    )
    remaining_capital = min(
        allocatable,
        buying_power,
        per_cycle,
    )
    remaining_buying_power = buying_power
    maximum_symbol = portfolio_value * decimal_or_zero(
        settings.get(
            "maximum_single_position_weight"
        )
    )
    maximum_sector = portfolio_value * decimal_or_zero(
        settings.get("maximum_sector_weight")
    )
    minimum_order_value = decimal_or_zero(
        settings.get("minimum_order_value")
    )
    maximum_order_count = int(
        settings.get("maximum_order_count", 0)
    )
    fractional_precision = int(
        order_policy.settings.get(
            "fractional_quantity_precision",
            6,
        )
    )
    price_precision = int(
        order_policy.settings.get(
            "price_precision",
            2,
        )
    )
    market_phase = str(
        snapshot.get("market_phase", "unknown")
    )
    positions = _positions_by_symbol(snapshot)
    quotes = _mapping(snapshot.get("quotes"))
    assets = _mapping(snapshot.get("assets"))
    portfolio = _portfolio_metadata(
        portfolio_output
    )
    indexed_decisions = list(
        enumerate(
            _records(
                execution_output.get("decisions")
            )
        )
    )
    indexed_decisions.sort(
        key=lambda item: _decision_sort_key(
            item,
            portfolio,
        )
    )
    protection_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in _records(
            execution_output.get(
                "protection_plans"
            )
        )
        if item.get("symbol")
    }

    sector_potential: dict[str, Decimal] = {}
    exposure_cache: dict[
        str,
        tuple[
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            bool,
        ],
    ] = {}
    for _, decision in indexed_decisions:
        symbol = str(
            decision.get("symbol", "")
        ).upper()
        quote = _mapping(quotes.get(symbol))
        position = _mapping(positions.get(symbol))
        exposure_cache[symbol] = _exposure(
            symbol=symbol,
            position=position,
            open_orders=open_orders,
            quote=quote,
        )
        metadata = _mapping(portfolio.get(symbol))
        sector = str(
            metadata.get("sector", "unknown")
        ) or "unknown"
        sector_key = (
            sector
            if sector != "unknown"
            else f"unknown:{symbol}"
        )
        sector_potential[sector_key] = (
            sector_potential.get(
                sector_key,
                ZERO,
            )
            + exposure_cache[symbol][3]
        )

    proposed_count = 0
    orders: list[ProposedOrder] = []
    for intent_index, decision in indexed_decisions:
        symbol = str(
            decision.get("symbol", "")
        ).upper()
        decision_state = str(
            decision.get("execution_decision", "")
        )
        if decision_state not in EXECUTABLE_DECISIONS:
            orders.append(
                _empty_order(
                    paths=paths,
                    decision=decision,
                    intent_index=intent_index,
                    order_policy=order_policy,
                    status=OrderStatus.SKIPPED,
                    reason=(
                        f"execution_{decision_state or 'invalid'}"
                    ),
                    market_phase=market_phase,
                )
            )
            continue

        quote = _mapping(quotes.get(symbol))
        asset = _mapping(assets.get(symbol))
        asset_class = str(
            asset.get("asset_class", "")
        )
        is_crypto = asset_class == "crypto"
        protection_plan = _mapping(
            protection_by_symbol.get(symbol)
        )
        queued_protected_entry = (
            not is_crypto
            and market_phase in EXTENDED_PHASES
            and str(
                protection_plan.get(
                    "apply_to",
                    "",
                )
            )
            in {"new_entry", "both"}
            and str(
                protection_plan.get(
                    "mode",
                    "none",
                )
            )
            != "none"
            and str(
                decision.get(
                    "side",
                    "none",
                )
            )
            == "buy"
        )
        position = _mapping(positions.get(symbol))
        crypto_policy = _mapping(
            order_policy.settings.get("crypto")
        )
        automatic_crypto_liquidation = (
            is_automatic_crypto_liquidation_decision(
                decision,
                asset,
                crypto_policy,
            )
        )
        metadata = _mapping(portfolio.get(symbol))
        sector = str(
            metadata.get("sector", "unknown")
        ) or "unknown"
        sector_key = (
            sector
            if sector != "unknown"
            else f"unknown:{symbol}"
        )
        try:
            priority = int(
                metadata.get("priority", 999999)
            )
        except (TypeError, ValueError):
            priority = 999999
        (
            current,
            open_buy,
            open_sell,
            potential,
            open_sell_qty,
            exposure_complete,
        ) = exposure_cache[symbol]
        target = portfolio_value * decimal_or_zero(
            decision.get("target_weight")
        )
        raw_delta = target - potential
        fraction = decimal_or_zero(
            decision.get("execution_fraction")
        )
        execution_delta = raw_delta * fraction
        side = str(
            decision.get("side", "none")
        ).lower()
        price_condition = _mapping(
            decision.get("price_condition")
        )
        reference_kind = str(
            price_condition.get("reference", "none")
        )
        reference_price = _quote_reference(
            quote,
            reference_kind,
        )
        if (
            reference_price is None
            and automatic_crypto_liquidation
        ):
            reference_price = decimal_or_zero(
                position.get("current_price")
            )
            if reference_price <= ZERO:
                reference_price = decimal_or_zero(
                    position.get(
                        "average_entry_price"
                    )
                )
            if reference_price <= ZERO:
                reference_price = None
        order_intent = _mapping(
            decision.get("order_intent")
        )
        order_type = str(
            order_intent.get(
                "preferred_type",
                "none",
            )
        ).lower()
        time_in_force = str(
            order_intent.get(
                "time_in_force_preference",
                order_policy.settings.get(
                    "default_time_in_force",
                    "day",
                ),
            )
        ).lower()
        requested_extended = bool(
            order_intent.get(
                "extended_hours_requested"
            )
        )
        extended = market_phase in EXTENDED_PHASES
        reasons: list[str] = []
        status = OrderStatus.PROPOSED

        if global_issues:
            status = OrderStatus.BLOCKED
            reasons.extend(global_issues)
        if not exposure_complete:
            status = OrderStatus.BLOCKED
            reasons.append(
                "open_order_reference_price_missing"
            )
        if reference_price is None:
            status = OrderStatus.BLOCKED
            reasons.append("reference_price_missing")
            reference_price = ZERO
        if asset.get("status") != "active":
            status = OrderStatus.BLOCKED
            reasons.append("asset_not_active")
        if asset.get("tradable") is not True:
            status = OrderStatus.BLOCKED
            reasons.append("asset_not_tradable")
        if (
            market_phase
            in {"overnight", "overnight_session"}
            and not is_crypto
            and not queued_protected_entry
            and (
                asset.get("overnight_tradable")
                is not True
                or asset.get("overnight_halted") is True
            )
        ):
            status = OrderStatus.BLOCKED
            reasons.append(
                "asset_not_overnight_tradable"
            )
        supported_classes = set(
            order_policy.settings.get(
                "supported_asset_classes",
                [],
            )
        )
        if (
            str(asset.get("asset_class", ""))
            not in supported_classes
        ):
            status = OrderStatus.BLOCKED
            reasons.append("asset_class_unsupported")
        if (
            market_phase == "unknown"
            and not is_crypto
        ):
            status = OrderStatus.BLOCKED
            reasons.append("market_phase_unknown")
        elif (
            market_phase in CLOSED_PHASES
            and not is_crypto
        ):
            queue = _mapping(
                order_policy.settings.get(
                    "queue_policy"
                )
            )
            closed_policy = _mapping(
                order_policy.settings.get(
                    "closed_session_queue"
                )
            )
            closed_fraction_limit = decimal_or_zero(
                closed_policy.get(
                    "maximum_open_execution_fraction"
                    if str(
                        decision.get(
                            "portfolio_action",
                            "",
                        )
                    )
                    in {"open", "increase"}
                    else "maximum_reduce_execution_fraction"
                )
            )
            capabilities = _mapping(
                snapshot.get(
                    "broker_capabilities"
                )
            )
            if not (
                queue.get(market_phase) is True
                and capabilities.get(
                    "supports_closed_session_queue"
                )
                is True
                and order_intent.get("allow_queue")
                is True
                and order_type
                in set(
                    closed_policy.get(
                        "supported_order_types",
                        [],
                    )
                )
                and time_in_force
                in set(
                    closed_policy.get(
                        "supported_time_in_force",
                        [],
                    )
                )
                and requested_extended is False
                and fraction
                <= closed_fraction_limit
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "closed_session_queue_unsupported"
                )
        if is_crypto:
            supported_crypto = set(
                _mapping(
                    order_policy.settings.get(
                        "supported_order_types"
                    )
                ).get("crypto", [])
            )
            if (
                order_type
                not in supported_crypto
                or time_in_force
                not in set(
                    crypto_policy.get(
                        "supported_time_in_force",
                        [],
                    )
                )
                or requested_extended
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "crypto_order_intent_unsupported"
                )
            if (
                side == "buy"
                and not position
                and crypto_policy.get(
                    "allow_new_positions"
                )
                is not True
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "crypto_new_position_forbidden"
                )
            if (
                side != "sell"
                or str(
                    decision.get(
                        "portfolio_action",
                        "",
                    )
                )
                not in {"reduce", "close"}
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "crypto_position_expansion_forbidden"
                )
            if (
                decimal_or_zero(
                    asset.get("min_order_size")
                )
                <= ZERO
                or decimal_or_zero(
                    asset.get("min_trade_increment")
                )
                <= ZERO
                or (
                    order_type == "limit"
                    and decimal_or_zero(
                        asset.get("price_increment")
                    )
                    <= ZERO
                )
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "crypto_asset_increment_missing"
                )
        elif extended:
            supported_extended = set(
                _mapping(
                    order_policy.settings.get(
                        "supported_order_types"
                    )
                ).get("extended_hours", [])
            )
            if queued_protected_entry:
                if order_type != "limit":
                    status = OrderStatus.BLOCKED
                    reasons.append(
                        "protected_entry_queue_requires_limit"
                    )
            elif (
                not requested_extended
                or order_type
                not in supported_extended
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "extended_hours_intent_unsupported"
                )
        else:
            supported_regular = set(
                _mapping(
                    order_policy.settings.get(
                        "supported_order_types"
                    )
                ).get("regular_session", [])
            )
            if (
                market_phase == "regular_session"
                and (
                    order_type
                    not in supported_regular
                    or time_in_force
                    not in set(
                        order_policy.settings.get(
                            "supported_time_in_force",
                            [],
                        )
                    )
                )
            ):
                status = OrderStatus.BLOCKED
                reasons.append(
                    "regular_order_type_unsupported"
                )

        limit_price: Decimal | None = None
        if order_type == "limit":
            raw_limit = price_condition.get(
                "limit_price"
            )
            limit_price = (
                decimal_or_zero(raw_limit)
                if raw_limit is not None
                else reference_price
            )
            if limit_price <= ZERO:
                status = OrderStatus.BLOCKED
                reasons.append("limit_price_invalid")
                limit_price = None
            elif is_crypto:
                price_increment = decimal_or_zero(
                    asset.get("price_increment")
                )
                limit_price = (
                    _floor_to_increment(
                        limit_price,
                        price_increment,
                    )
                    if side == "buy"
                    else _ceil_to_increment(
                        limit_price,
                        price_increment,
                    )
                )
            else:
                limit_price = _floor(
                    limit_price,
                    price_precision,
                )
        boundary = price_condition.get(
            "do_not_execute_above"
        )
        if (
            side == "buy"
            and boundary is not None
            and reference_price
            > decimal_or_zero(boundary)
        ):
            status = OrderStatus.BLOCKED
            reasons.append(
                "do_not_execute_above_breached"
            )

        quantity = ZERO
        planned_value = ZERO
        if (
            side == "buy"
            and execution_delta > ZERO
            and reference_price > ZERO
        ):
            symbol_capacity = max(
                maximum_symbol - potential,
                ZERO,
            )
            sector_capacity = max(
                maximum_sector
                - sector_potential.get(
                    sector_key,
                    ZERO,
                ),
                ZERO,
            )
            allowed_value = min(
                execution_delta,
                remaining_capital,
                remaining_buying_power,
                symbol_capacity,
                sector_capacity,
            )
            raw_quantity = (
                allowed_value / reference_price
            )
            quantity = (
                _floor_to_increment(
                    raw_quantity,
                    decimal_or_zero(
                        asset.get(
                            "min_trade_increment"
                        )
                    ),
                )
                if is_crypto
                else _floor(
                    raw_quantity,
                    (
                        fractional_precision
                        if asset.get("fractionable")
                        is True
                        else 0
                    ),
                )
            )
            planned_value = (
                quantity * reference_price
            )
        elif (
            side == "sell"
            and execution_delta < ZERO
            and reference_price > ZERO
        ):
            available = max(
                decimal_or_zero(
                    position.get(
                        "available_quantity"
                    )
                )
                - open_sell_qty,
                ZERO,
            )
            desired = (
                available
                if str(
                    decision.get(
                        "portfolio_action", ""
                    )
                )
                == "close"
                else min(
                    abs(execution_delta)
                    / reference_price,
                    available,
                )
            )
            quantity = (
                _floor_to_increment(
                    desired,
                    decimal_or_zero(
                        asset.get(
                            "min_trade_increment"
                        )
                    ),
                )
                if is_crypto
                else _floor(
                    desired,
                    (
                        fractional_precision
                        if asset.get("fractionable")
                        is True
                        else 0
                    ),
                )
            )
            planned_value = (
                quantity * reference_price
            )
        else:
            status = OrderStatus.SKIPPED
            reasons.append("target_already_covered")

        if is_crypto:
            trade_increment = decimal_or_zero(
                asset.get("min_trade_increment")
            )
            quantity = _floor_to_increment(
                quantity,
                trade_increment,
            )
            planned_value = (
                quantity * reference_price
            )
            minimum_quantity = decimal_or_zero(
                asset.get("min_order_size")
            )
            if (
                status == OrderStatus.PROPOSED
                and minimum_quantity > ZERO
                and quantity < minimum_quantity
            ):
                status = OrderStatus.SKIPPED
                reasons.append(
                    "below_asset_min_order_size"
                )

        if (
            status == OrderStatus.PROPOSED
            and (
                quantity <= ZERO
                or planned_value < minimum_order_value
            )
        ):
            status = OrderStatus.SKIPPED
            reasons.append(
                "below_minimum_order_value"
                if planned_value > ZERO
                else "quantity_quantized_to_zero"
            )
        same_side = [
            item
            for item in open_orders
            if str(item.get("symbol", "")).upper()
            == symbol
            and not is_system_protective_order(
                item
            )
            and str(item.get("side", "")).lower()
            == side
            and (
                _remaining_quantity(item) or ZERO
            )
            > ZERO
        ]
        opposite_side = [
            item
            for item in open_orders
            if str(item.get("symbol", "")).upper()
            == symbol
            and not is_system_protective_order(
                item
            )
            and str(item.get("side", "")).lower()
            not in {side, ""}
            and (
                _remaining_quantity(item) or ZERO
            )
            > ZERO
        ]
        dependencies = action_dependencies.get(
            symbol,
            (),
        )
        if (
            status == OrderStatus.PROPOSED
            and dependencies
        ):
            status = OrderStatus.DEPENDENT
            reasons.append(
                "replacement_requires_cancel_refresh"
            )
        elif (
            status == OrderStatus.PROPOSED
            and opposite_side
        ):
            status = OrderStatus.DEPENDENT
            reasons.append(
                "opposite_side_open_order_conflict"
            )
        elif (
            status == OrderStatus.PROPOSED
            and same_side
        ):
            status = OrderStatus.DEPENDENT
            reasons.append(
                "same_side_open_order_requires_refresh"
            )
        if (
            status == OrderStatus.PROPOSED
            and proposed_count >= maximum_order_count
        ):
            status = OrderStatus.SKIPPED
            reasons.append("maximum_order_count")

        version = str(
            order_policy.settings.get(
                "idempotency_version",
                "1",
            )
        )
        plan_id = build_plan_id(
            profile_id=paths.profile_id,
            strategy_id=paths.strategy_id,
            strategy_version=paths.strategy_version,
            cycle_id=paths.cycle_id,
            symbol=symbol,
            side=side,
            intent_index=intent_index,
            order_role="primary",
            idempotency_version=version,
        )
        client_id = build_client_order_id(
            profile_id=paths.profile_id,
            strategy_id=paths.strategy_id,
            strategy_version=paths.strategy_version,
            cycle_id=paths.cycle_id,
            symbol=symbol,
            side=side,
            intent_index=intent_index,
            order_role="primary",
            idempotency_version=version,
            max_length=int(
                order_policy.settings.get(
                    "client_order_id_max_length",
                    48,
                )
            ),
        )
        order = ProposedOrder(
            plan_id=plan_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            planned_value=planned_value,
            reference_price=reference_price,
            limit_price=limit_price,
            time_in_force=time_in_force,
            extended_hours=(
                extended
                and not is_crypto
                and not queued_protected_entry
            ),
            client_order_id=client_id,
            status=status,
            reason_codes=tuple(
                dict.fromkeys(reasons)
            ),
            current_position_value=current,
            open_buy_remaining_value=open_buy,
            open_sell_remaining_value=open_sell,
            potential_position_value=potential,
            target_position_value=target,
            raw_delta_value=raw_delta,
            execution_delta_value=execution_delta,
            sector=sector,
            fractionable=bool(
                asset.get("fractionable")
            ),
            market_phase=market_phase,
            urgency=str(
                decision.get("urgency", "none")
            ),
            priority=priority,
            conviction=str(
                metadata.get("conviction", "none")
            ),
            depends_on=dependencies,
            price_condition={
                key: (
                    decimal_text(
                        decimal_value(value)
                    )
                    if key
                    in {
                        "limit_price",
                        "do_not_execute_above",
                        "review_below",
                    }
                    and value is not None
                    else value
                )
                for key, value
                in price_condition.items()
            },
        )
        orders.append(order)
        if status == OrderStatus.PROPOSED:
            proposed_count += 1
            if side == "buy":
                remaining_capital = max(
                    remaining_capital
                    - planned_value,
                    ZERO,
                )
                remaining_buying_power = max(
                    remaining_buying_power
                    - planned_value,
                    ZERO,
                )
                sector_potential[sector_key] = (
                    sector_potential.get(
                        sector_key,
                        ZERO,
                    )
                    + planned_value
                )

    (
        protected_orders,
        protection_actions,
        protection_warnings,
    ) = apply_protection_plans(
        paths=paths,
        execution_output=execution_output,
        snapshot=snapshot,
        order_policy=order_policy,
        permission=permission,
        primary_orders=orders,
        market_phase=market_phase,
        fractional_precision=fractional_precision,
        price_precision=price_precision,
    )

    return ProposedOrderPlan(
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
        risk_profile=risk_profile.reference,
        order_policy=order_policy.reference,
        run_date=paths.run_date,
        cycle_id=paths.cycle_id,
        generated_at=generated_at or utc_now_iso(),
        execution_output_hash=canonical_hash(
            execution_output
        ),
        pretrade_snapshot_hash=(
            snapshot_model.snapshot_hash
        ),
        permission=permission,
        orders=protected_orders,
        actions=tuple(
            (*actions, *protection_actions)
        ),
        warnings=protection_warnings,
        global_issues=tuple(global_issues),
    )
