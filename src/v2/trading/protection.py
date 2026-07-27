"""把 Codex 的持仓保护意图转换为 Alpaca 可接受的订单组合。

作用：为既有持仓和新建仓生成 stop、stop-limit、take-profit、trailing、
OCO、OTO、bracket 与分级 OCO，并对碎股或时段不兼容组合确定性降级。
重要性：Codex 可以选择策略，但数量、价格精度、券商能力、幂等和替换始终由
Python 控制；本模块只生成 Stage F 计划，不接触 Alpaca 写接口。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping, Sequence

from v2.data.orders import is_system_protective_order
from v2.models.orders import (
    ZERO,
    OrderAction,
    OrderStatus,
    ProposedOrder,
    ProposedOrderAction,
    SubmissionPermission,
    decimal_or_zero,
    decimal_text,
)
from v2.profiles import OrderPolicy
from v2.runtime import CyclePaths
from v2.trading.idempotency import (
    build_client_order_id,
    build_plan_id,
)


ACTIVE_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "held",
    "pending_replace",
    "accepted_for_bidding",
}
ADVANCED_MODES = {
    "oco",
    "bracket",
    "staged_oco",
}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
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


def _positive(
    value: object,
    *,
    precision: int | None = None,
) -> Decimal | None:
    result = decimal_or_zero(value)
    if result <= ZERO:
        return None
    return (
        _floor(result, precision)
        if precision is not None
        else result
    )


def _remaining_quantity(
    order: Mapping[str, Any],
) -> Decimal:
    if order.get("remaining_quantity") is not None:
        return max(
            decimal_or_zero(
                order.get("remaining_quantity")
            ),
            ZERO,
        )
    return max(
        decimal_or_zero(order.get("quantity"))
        - decimal_or_zero(
            order.get("filled_quantity")
        ),
        ZERO,
    )


def _reference_price(
    *,
    symbol: str,
    position: Mapping[str, Any],
    quotes: Mapping[str, Any],
    primary_orders: Sequence[ProposedOrder],
) -> Decimal:
    quote = _mapping(quotes.get(symbol))
    primary = next(
        (
            item
            for item in primary_orders
            if item.symbol == symbol
            and item.side == "buy"
            and item.status == OrderStatus.PROPOSED
        ),
        None,
    )
    for value in (
        position.get("current_price"),
        quote.get("midpoint"),
        primary.reference_price
        if primary is not None
        else None,
        position.get("average_entry_price"),
    ):
        parsed = decimal_or_zero(value)
        if parsed > ZERO:
            return parsed
    return ZERO


def _is_fractional(quantity: Decimal) -> bool:
    return quantity != quantity.to_integral_value()


def _role(mode: str, index: int) -> str:
    compact = {
        "stop": "pt-stp",
        "stop_limit": "pt-stpl",
        "take_profit": "pt-tp",
        "trailing_stop": "pt-trl",
        "oco": "pt-oco",
        "bracket": "pt-brk",
        "oto_stop": "pt-otos",
        "oto_take_profit": "pt-otot",
        "staged_oco": "pt-soco",
    }.get(mode, "pt-safe")
    return f"{compact}{index}" if index else compact


def _identity(
    *,
    paths: CyclePaths,
    order_policy: OrderPolicy,
    symbol: str,
    side: str,
    intent_index: int,
    role: str,
) -> tuple[str, str]:
    version = str(
        order_policy.settings.get(
            "idempotency_version",
            "1",
        )
    )
    common = {
        "profile_id": paths.profile_id,
        "strategy_id": paths.strategy_id,
        "strategy_version": paths.strategy_version,
        "cycle_id": paths.cycle_id,
        "symbol": symbol,
        "side": side,
        "intent_index": intent_index,
        "order_role": role,
        "idempotency_version": version,
    }
    return (
        build_plan_id(**common),
        build_client_order_id(
            **common,
            max_length=int(
                order_policy.settings.get(
                    "client_order_id_max_length",
                    48,
                )
            ),
        ),
    )


def _protective_order(
    *,
    paths: CyclePaths,
    order_policy: OrderPolicy,
    symbol: str,
    quantity: Decimal,
    reference_price: Decimal,
    time_in_force: str,
    mode: str,
    intent_index: int,
    fractionable: bool,
    market_phase: str,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    trail_price: Decimal | None = None,
    trail_percent: Decimal | None = None,
    take_profit_price: Decimal | None = None,
    stop_loss_price: Decimal | None = None,
    stop_loss_limit_price: Decimal | None = None,
    order_class: str = "simple",
    reasons: Sequence[str] = (),
    depends_on: Sequence[str] = (),
) -> ProposedOrder:
    role = _role(mode, intent_index)
    plan_id, client_id = _identity(
        paths=paths,
        order_policy=order_policy,
        symbol=symbol,
        side="sell",
        intent_index=intent_index,
        role=role,
    )
    order_type = {
        "stop": "stop",
        "stop_limit": "stop_limit",
        "take_profit": "limit",
        "trailing_stop": "trailing_stop",
        "oco": "limit",
        "bracket": "limit",
        "oto_stop": "stop",
        "oto_take_profit": "limit",
        "staged_oco": "limit",
    }.get(mode, "stop")
    status = (
        OrderStatus.DEPENDENT
        if depends_on
        else OrderStatus.PROPOSED
    )
    return ProposedOrder(
        plan_id=plan_id,
        symbol=symbol,
        side="sell",
        order_type=order_type,
        quantity=quantity,
        planned_value=quantity * reference_price,
        reference_price=reference_price,
        limit_price=limit_price,
        time_in_force=time_in_force,
        extended_hours=False,
        client_order_id=client_id,
        status=status,
        reason_codes=tuple(
            dict.fromkeys(
                (
                    "codex_position_protection",
                    *reasons,
                    *(
                        (
                            "replacement_requires_cancel_refresh",
                        )
                        if depends_on
                        else ()
                    ),
                )
            )
        ),
        current_position_value=(
            quantity * reference_price
        ),
        open_buy_remaining_value=ZERO,
        open_sell_remaining_value=ZERO,
        potential_position_value=(
            quantity * reference_price
        ),
        target_position_value=(
            quantity * reference_price
        ),
        raw_delta_value=ZERO,
        execution_delta_value=ZERO,
        sector="protection",
        fractionable=fractionable,
        market_phase=market_phase,
        urgency="high",
        priority=-1,
        conviction="high",
        depends_on=tuple(depends_on),
        price_condition={},
        order_class=order_class,
        stop_price=stop_price,
        trail_price=trail_price,
        trail_percent=trail_percent,
        take_profit_limit_price=(
            take_profit_price
        ),
        stop_loss_stop_price=stop_loss_price,
        stop_loss_limit_price=(
            stop_loss_limit_price
        ),
        protection_role=role,
    )


def _stage_prices(
    plan: Mapping[str, Any],
    *,
    price_precision: int,
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
]:
    stages = _records(plan.get("stages"))
    take_profits = [
        value
        for item in stages
        if (
            value := _positive(
                item.get("take_profit_price"),
                precision=price_precision,
            )
        )
        is not None
    ]
    stops = [
        value
        for item in stages
        if (
            value := _positive(
                item.get("stop_price"),
                precision=price_precision,
            )
        )
        is not None
    ]
    limits = [
        value
        for item in stages
        if (
            value := _positive(
                item.get("stop_limit_price"),
                precision=price_precision,
            )
        )
        is not None
    ]
    return (
        min(take_profits) if take_profits else None,
        max(stops) if stops else None,
        max(limits) if limits else None,
    )


def _fixed_stop_from_trail(
    plan: Mapping[str, Any],
    *,
    reference_price: Decimal,
    price_precision: int,
) -> Decimal | None:
    trail_price = _positive(plan.get("trail_price"))
    trail_percent = _positive(
        plan.get("trail_percent")
    )
    if trail_price is not None:
        raw = reference_price - trail_price
    elif trail_percent is not None:
        raw = reference_price * (
            Decimal("1")
            - trail_percent / Decimal("100")
        )
    else:
        return None
    return (
        _floor(raw, price_precision)
        if raw > ZERO
        else None
    )


def _simple_or_advanced_orders(
    *,
    paths: CyclePaths,
    order_policy: OrderPolicy,
    plan: Mapping[str, Any],
    symbol: str,
    base_quantity: Decimal,
    reference_price: Decimal,
    fractionable: bool,
    market_phase: str,
    fractional_precision: int,
    price_precision: int,
    depends_on: Sequence[str] = (),
) -> tuple[list[ProposedOrder], list[str]]:
    mode = str(plan.get("mode", "none"))
    warnings: list[str] = []
    mode_reasons: list[str] = []
    coverage = decimal_or_zero(
        plan.get("coverage_fraction")
    )
    quantity = _floor(
        base_quantity * coverage,
        (
            fractional_precision
            if fractionable
            else 0
        ),
    )
    if quantity <= ZERO or mode == "none":
        return [], warnings
    time_in_force = str(
        plan.get("time_in_force", "day")
    )
    fractional = _is_fractional(quantity)
    take_profit = _positive(
        plan.get("take_profit_price"),
        precision=price_precision,
    )
    stop = _positive(
        plan.get("stop_price"),
        precision=price_precision,
    )
    stop_limit = _positive(
        plan.get("stop_limit_price"),
        precision=price_precision,
    )
    trail_price = _positive(
        plan.get("trail_price"),
        precision=price_precision,
    )
    trail_percent = _positive(
        plan.get("trail_percent")
    )

    effective_mode = mode
    if fractional:
        time_in_force = "day"
        if mode in ADVANCED_MODES:
            effective_mode = (
                "stop_limit"
                if stop_limit is not None
                else "stop"
            )
            if mode == "staged_oco":
                _, stage_stop, stage_limit = (
                    _stage_prices(
                        plan,
                        price_precision=price_precision,
                    )
                )
                stop = stage_stop
                stop_limit = stage_limit
                effective_mode = (
                    "stop_limit"
                    if stop_limit is not None
                    else "stop"
                )
            warnings.append(
                f"{symbol}:fractional_{mode}_downgraded_to_{effective_mode}"
            )
            mode_reasons.append(
                f"fractional_{mode}_downgraded_to_{effective_mode}"
            )
        elif mode == "trailing_stop":
            stop = _fixed_stop_from_trail(
                plan,
                reference_price=reference_price,
                price_precision=price_precision,
            )
            effective_mode = "stop"
            warnings.append(
                f"{symbol}:fractional_trailing_stop_downgraded_to_stop"
            )
            mode_reasons.append(
                "fractional_trailing_stop_downgraded_to_stop"
            )
        elif mode == "oto_stop":
            effective_mode = (
                "stop_limit"
                if stop_limit is not None
                else "stop"
            )
        elif mode == "oto_take_profit":
            effective_mode = "take_profit"

    common = {
        "paths": paths,
        "order_policy": order_policy,
        "symbol": symbol,
        "reference_price": reference_price,
        "time_in_force": time_in_force,
        "fractionable": fractionable,
        "market_phase": market_phase,
        "depends_on": depends_on,
    }
    if effective_mode == "staged_oco":
        result: list[ProposedOrder] = []
        assigned = ZERO
        stages = _records(plan.get("stages"))
        for index, stage in enumerate(stages):
            stage_fraction = decimal_or_zero(
                stage.get("coverage_fraction")
            )
            stage_quantity = _floor(
                base_quantity * stage_fraction,
                (
                    fractional_precision
                    if fractionable
                    else 0
                ),
            )
            if index == len(stages) - 1:
                stage_quantity = min(
                    max(quantity - assigned, ZERO),
                    stage_quantity,
                )
            if stage_quantity <= ZERO:
                continue
            assigned += stage_quantity
            stage_tp = _positive(
                stage.get("take_profit_price"),
                precision=price_precision,
            )
            stage_stop = _positive(
                stage.get("stop_price"),
                precision=price_precision,
            )
            stage_limit = _positive(
                stage.get("stop_limit_price"),
                precision=price_precision,
            )
            result.append(
                _protective_order(
                    **common,
                    quantity=stage_quantity,
                    mode=effective_mode,
                    intent_index=index,
                    limit_price=stage_tp,
                    take_profit_price=stage_tp,
                    stop_loss_price=stage_stop,
                    stop_loss_limit_price=stage_limit,
                    order_class="oco",
                    reasons=(
                        *mode_reasons,
                        "staged_oco_exit",
                    ),
                )
            )
        return result, warnings
    if effective_mode in {"oco", "bracket"}:
        return [
            _protective_order(
                **common,
                quantity=quantity,
                mode=effective_mode,
                intent_index=0,
                limit_price=take_profit,
                take_profit_price=take_profit,
                stop_loss_price=stop,
                stop_loss_limit_price=stop_limit,
                order_class="oco",
                reasons=(
                    *mode_reasons,
                    (
                        "existing_bracket_mapped_to_oco"
                        if effective_mode == "bracket"
                        else "oco_exit"
                    ),
                ),
            )
        ], warnings
    if effective_mode in {"stop", "oto_stop"}:
        return [
            _protective_order(
                **common,
                quantity=quantity,
                mode=effective_mode,
                intent_index=0,
                stop_price=stop,
                reasons=(
                    *mode_reasons,
                    "fixed_stop_exit",
                ),
            )
        ], warnings
    if effective_mode == "stop_limit":
        return [
            _protective_order(
                **common,
                quantity=quantity,
                mode=effective_mode,
                intent_index=0,
                stop_price=stop,
                limit_price=stop_limit,
                reasons=(
                    *mode_reasons,
                    "stop_limit_exit",
                ),
            )
        ], warnings
    if effective_mode in {
        "take_profit",
        "oto_take_profit",
    }:
        return [
            _protective_order(
                **common,
                quantity=quantity,
                mode=effective_mode,
                intent_index=0,
                limit_price=take_profit,
                reasons=(
                    *mode_reasons,
                    "take_profit_exit",
                ),
            )
        ], warnings
    if effective_mode == "trailing_stop":
        return [
            _protective_order(
                **common,
                quantity=quantity,
                mode=effective_mode,
                intent_index=0,
                trail_price=trail_price,
                trail_percent=trail_percent,
                reasons=(
                    *mode_reasons,
                    "trailing_stop_exit",
                ),
            )
        ], warnings
    return [], warnings


def _decimal_signature(value: object) -> str:
    parsed = decimal_or_zero(value)
    return decimal_text(parsed) if parsed > ZERO else ""


def _desired_signature(
    order: ProposedOrder,
) -> tuple[str, ...]:
    return (
        order.order_class,
        order.order_type,
        decimal_text(order.quantity),
        order.time_in_force,
        _decimal_signature(order.limit_price),
        _decimal_signature(order.stop_price),
        _decimal_signature(order.trail_price),
        _decimal_signature(order.trail_percent),
        _decimal_signature(
            order.take_profit_limit_price
        ),
        _decimal_signature(
            order.stop_loss_stop_price
        ),
        _decimal_signature(
            order.stop_loss_limit_price
        ),
    )


def _existing_signature(
    order: Mapping[str, Any],
) -> tuple[str, ...]:
    order_class = str(
        order.get("order_class", "simple")
    ).lower()
    order_type = str(
        order.get("type", "")
    ).lower()
    take_profit = order.get("limit_price")
    stop_loss = None
    stop_loss_limit = None
    if order_class in {"oco", "bracket", "oto"}:
        for leg in _records(order.get("legs")):
            leg_type = str(
                leg.get("type", "")
            ).lower()
            if leg_type in {"stop", "stop_limit"}:
                stop_loss = leg.get("stop_price")
                stop_loss_limit = leg.get(
                    "limit_price"
                )
            elif leg_type == "limit":
                take_profit = leg.get(
                    "limit_price"
                )
    return (
        order_class,
        order_type,
        _decimal_signature(
            _remaining_quantity(order)
        ),
        str(order.get("time_in_force", "")).lower(),
        _decimal_signature(order.get("limit_price")),
        _decimal_signature(order.get("stop_price")),
        _decimal_signature(order.get("trail_price")),
        _decimal_signature(
            order.get("trail_percent")
        ),
        _decimal_signature(take_profit),
        _decimal_signature(stop_loss),
        _decimal_signature(stop_loss_limit),
    )


def _replacement_actions(
    *,
    paths: CyclePaths,
    order_policy: OrderPolicy,
    permission: SubmissionPermission,
    symbol: str,
    existing: Sequence[Mapping[str, Any]],
) -> tuple[ProposedOrderAction, ...]:
    result: list[ProposedOrderAction] = []
    version = str(
        order_policy.settings.get(
            "idempotency_version",
            "1",
        )
    )
    for index, item in enumerate(existing):
        reference = str(
            item.get("broker_order_id")
            or item.get("client_order_id")
            or ""
        )
        action_id = build_plan_id(
            profile_id=paths.profile_id,
            strategy_id=paths.strategy_id,
            strategy_version=paths.strategy_version,
            cycle_id=paths.cycle_id,
            symbol=symbol,
            side="sell",
            intent_index=index,
            order_role="action-pt-replace",
            idempotency_version=version,
        )
        result.append(
            ProposedOrderAction(
                action_id=action_id,
                order_reference=reference,
                symbol=symbol,
                action=OrderAction.REPLACE,
                status=OrderStatus.DEPENDENT,
                reason=(
                    "protective_strategy_changed_cancel_then_refresh"
                ),
                broker_order_id=(
                    str(item.get("broker_order_id"))
                    if item.get("broker_order_id")
                    else None
                ),
                client_order_id=(
                    str(item.get("client_order_id"))
                    if item.get("client_order_id")
                    else None
                ),
            )
        )
    return tuple(result)


def _attach_to_new_entries(
    *,
    plans: Mapping[str, Mapping[str, Any]],
    orders: Sequence[ProposedOrder],
    price_precision: int,
) -> tuple[list[ProposedOrder], list[str]]:
    result: list[ProposedOrder] = []
    warnings: list[str] = []
    for order in orders:
        plan = plans.get(order.symbol)
        if (
            plan is None
            or order.side != "buy"
            or order.status != OrderStatus.PROPOSED
            or str(plan.get("apply_to"))
            not in {"new_entry", "both"}
            or str(plan.get("mode")) == "none"
        ):
            result.append(order)
            continue
        if _is_fractional(order.quantity):
            result.append(order)
            warnings.append(
                f"{order.symbol}:fractional_entry_protection_deferred_until_fill"
            )
            continue
        mode = str(plan.get("mode"))
        take_profit = _positive(
            plan.get("take_profit_price"),
            precision=price_precision,
        )
        stop = _positive(
            plan.get("stop_price"),
            precision=price_precision,
        )
        stop_limit = _positive(
            plan.get("stop_limit_price"),
            precision=price_precision,
        )
        if mode == "staged_oco":
            take_profit, stop, stop_limit = (
                _stage_prices(
                    plan,
                    price_precision=price_precision,
                )
            )
            mode = "bracket"
            warnings.append(
                f"{order.symbol}:staged_entry_compressed_to_bracket"
            )
        if mode == "trailing_stop":
            stop = _fixed_stop_from_trail(
                plan,
                reference_price=order.reference_price,
                price_precision=price_precision,
            )
            mode = "oto_stop"
            warnings.append(
                f"{order.symbol}:entry_trailing_stop_downgraded_to_oto_stop"
            )
        if mode in {"oco", "bracket"}:
            result.append(
                replace(
                    order,
                    order_class="bracket",
                    extended_hours=False,
                    take_profit_limit_price=(
                        take_profit
                    ),
                    stop_loss_stop_price=stop,
                    stop_loss_limit_price=(
                        stop_limit
                    ),
                    protection_role="pt-bracket",
                    reason_codes=tuple(
                        dict.fromkeys(
                            (
                                *order.reason_codes,
                                "codex_entry_bracket",
                            )
                        )
                    ),
                )
            )
        elif mode in {"stop", "stop_limit", "oto_stop"}:
            result.append(
                replace(
                    order,
                    order_class="oto",
                    extended_hours=False,
                    stop_loss_stop_price=stop,
                    stop_loss_limit_price=(
                        stop_limit
                        if mode == "stop_limit"
                        else None
                    ),
                    protection_role="pt-oto-stop",
                    reason_codes=tuple(
                        dict.fromkeys(
                            (
                                *order.reason_codes,
                                "codex_entry_oto_stop",
                            )
                        )
                    ),
                )
            )
        elif mode in {
            "take_profit",
            "oto_take_profit",
        }:
            result.append(
                replace(
                    order,
                    order_class="oto",
                    extended_hours=False,
                    take_profit_limit_price=(
                        take_profit
                    ),
                    protection_role="pt-oto-tp",
                    reason_codes=tuple(
                        dict.fromkeys(
                            (
                                *order.reason_codes,
                                "codex_entry_oto_take_profit",
                            )
                        )
                    ),
                )
            )
        else:
            result.append(order)
    return result, warnings


def apply_protection_plans(
    *,
    paths: CyclePaths,
    execution_output: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    order_policy: OrderPolicy,
    permission: SubmissionPermission,
    primary_orders: Sequence[ProposedOrder],
    market_phase: str,
    fractional_precision: int,
    price_precision: int,
) -> tuple[
    tuple[ProposedOrder, ...],
    tuple[ProposedOrderAction, ...],
    tuple[str, ...],
]:
    """Return entry-attached and standalone protections plus safe replacements."""

    capabilities = _mapping(
        order_policy.settings.get(
            "protective_orders"
        )
    )
    if capabilities.get("enabled") is not True:
        return tuple(primary_orders), (), ()
    plans = {
        str(item.get("symbol", "")).upper(): item
        for item in _records(
            execution_output.get("protection_plans")
        )
        if item.get("symbol")
    }
    attached, warnings = _attach_to_new_entries(
        plans=plans,
        orders=primary_orders,
        price_precision=price_precision,
    )
    positions = {
        str(item.get("symbol", "")).upper(): item
        for item in _records(snapshot.get("positions"))
        if item.get("symbol")
    }
    assets = _mapping(snapshot.get("assets"))
    quotes = _mapping(snapshot.get("quotes"))
    open_orders = [
        item
        for item in _records(
            snapshot.get("open_orders")
        )
        if str(item.get("status", "")).lower()
        in ACTIVE_ORDER_STATUSES
    ]
    generated: list[ProposedOrder] = []
    actions: list[ProposedOrderAction] = []
    for symbol, plan in sorted(plans.items()):
        if str(plan.get("apply_to")) not in {
            "existing_position",
            "both",
        }:
            continue
        position = _mapping(positions.get(symbol))
        asset = _mapping(assets.get(symbol))
        if (
            not position
            or asset.get("asset_class") == "crypto"
            or decimal_or_zero(
                position.get("quantity")
            )
            <= ZERO
        ):
            continue
        base_quantity = decimal_or_zero(
            position.get("quantity")
        )
        base_quantity = max(
            base_quantity
            - sum(
                (
                    item.quantity
                    for item in attached
                    if item.symbol == symbol
                    and item.side == "sell"
                    and item.status
                    == OrderStatus.PROPOSED
                    and item.protection_role
                    == "none"
                ),
                ZERO,
            ),
            ZERO,
        )
        for open_order in open_orders:
            if (
                str(
                    open_order.get(
                        "symbol",
                        "",
                    )
                ).upper()
                != symbol
                or str(
                    open_order.get(
                        "side",
                        "",
                    )
                ).lower()
                != "sell"
                or is_system_protective_order(
                    open_order
                )
            ):
                continue
            base_quantity = max(
                base_quantity
                - _remaining_quantity(open_order),
                ZERO,
            )
        reference = _reference_price(
            symbol=symbol,
            position=position,
            quotes=quotes,
            primary_orders=attached,
        )
        if base_quantity <= ZERO or reference <= ZERO:
            warnings.append(
                f"{symbol}:protection_not_built_without_quantity_or_reference"
            )
            continue
        desired, local_warnings = (
            _simple_or_advanced_orders(
                paths=paths,
                order_policy=order_policy,
                plan=plan,
                symbol=symbol,
                base_quantity=base_quantity,
                reference_price=reference,
                fractionable=bool(
                    asset.get("fractionable")
                ),
                market_phase=market_phase,
                fractional_precision=(
                    fractional_precision
                ),
                price_precision=price_precision,
            )
        )
        warnings.extend(local_warnings)
        existing = [
            item
            for item in open_orders
            if (
                str(item.get("symbol", "")).upper()
                == symbol
                and is_system_protective_order(
                    item
                )
            )
        ]
        incremental_buys = [
            item
            for item in attached
            if (
                item.symbol == symbol
                and item.side == "buy"
                and item.status == OrderStatus.PROPOSED
                and item.protection_role == "none"
            )
        ]
        if incremental_buys and not existing:
            buy_plan_ids = {
                item.plan_id
                for item in incremental_buys
            }
            attached = [
                (
                    replace(
                        item,
                        status=OrderStatus.DEPENDENT,
                        reason_codes=tuple(
                            dict.fromkeys(
                                (
                                    *item.reason_codes,
                                    "existing_position_protection_precedes_incremental_buy",
                                )
                            )
                        ),
                    )
                    if item.plan_id in buy_plan_ids
                    else item
                )
                for item in attached
            ]
            warnings.append(
                f"{symbol}:incremental_buy_deferred_until_existing_position_protected"
            )
        if sorted(
            _desired_signature(item)
            for item in desired
        ) == sorted(
            _existing_signature(item)
            for item in existing
        ) and not incremental_buys:
            warnings.append(
                f"{symbol}:existing_protection_unchanged"
            )
            continue
        dependencies: tuple[str, ...] = ()
        if existing:
            if incremental_buys:
                warnings.append(
                    f"{symbol}:existing_protection_cancel_required_before_incremental_buy"
                )
            replacements = _replacement_actions(
                paths=paths,
                order_policy=order_policy,
                permission=permission,
                symbol=symbol,
                existing=existing,
            )
            actions.extend(replacements)
            dependencies = tuple(
                item.action_id
                for item in replacements
            )
            attached = [
                (
                    replace(
                        item,
                        status=OrderStatus.DEPENDENT,
                        depends_on=tuple(
                            dict.fromkeys(
                                (
                                    *item.depends_on,
                                    *dependencies,
                                )
                            )
                        ),
                        reason_codes=tuple(
                            dict.fromkeys(
                                (
                                    *item.reason_codes,
                                    "protective_order_cancel_required",
                                )
                            )
                        ),
                    )
                    if item.symbol == symbol
                    and item.side == "sell"
                    and item.status
                    == OrderStatus.PROPOSED
                    and item.protection_role
                    == "none"
                    else item
                )
                for item in attached
            ]
            desired, local_warnings = (
                _simple_or_advanced_orders(
                    paths=paths,
                    order_policy=order_policy,
                    plan=plan,
                    symbol=symbol,
                    base_quantity=base_quantity,
                    reference_price=reference,
                    fractionable=bool(
                        asset.get("fractionable")
                    ),
                    market_phase=market_phase,
                    fractional_precision=(
                        fractional_precision
                    ),
                    price_precision=(
                        price_precision
                    ),
                    depends_on=dependencies,
                )
            )
            warnings.extend(local_warnings)
        generated.extend(desired)
    return (
        tuple((*attached, *generated)),
        tuple(actions),
        tuple(dict.fromkeys(warnings)),
    )
