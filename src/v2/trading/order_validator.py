"""对 Stage F 拟定订单执行独立于模型的 Python 硬校验。

作用：复核账户身份、快照顺序、配置 hash、资产能力、资本、数量、报价、市场规则、重复和依赖。
重要性：`--allow-trade` 只能改变合法计划的状态，不能绕过任何错误；Stage F 始终保持零提交。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from v2.crypto_liquidation import (
    is_automatic_crypto_liquidation_order,
)
from v2.data.orders import (
    is_system_protective_order,
)
from v2.models.orders import (
    ZERO,
    OrderStatus,
    OrderValidationIssue,
    PreTradeSnapshot,
    ProposedOrder,
    ProposedOrderPlan,
    ValidatedOrder,
    ValidatedOrderPlan,
    canonical_hash,
    decimal_or_zero,
)
from v2.profiles import OrderPolicy, RiskProfile
from v2.releases import sha256_file
from v2.runtime import utc_now_iso


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
VALID_MARKET_PHASES = {
    "regular_session",
    *EXTENDED_PHASES,
    *CLOSED_PHASES,
    "unknown",
}
ACTIVE_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "held",
    "pending_replace",
    "accepted_for_bidding",
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


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _issue(
    code: str,
    message: str,
    *,
    plan_id: str | None = None,
    path: str = "$",
    severity: str = "error",
) -> OrderValidationIssue:
    return OrderValidationIssue(
        code=code,
        message=message,
        severity=severity,
        path=path,
        plan_id=plan_id,
    )


def _precision_valid(
    value: Decimal,
    precision: int,
) -> bool:
    return value.as_tuple().exponent >= -precision


def _global_checks(
    *,
    plan: ProposedOrderPlan,
    execution_output: Mapping[str, Any],
    snapshot: PreTradeSnapshot,
    risk_profile: RiskProfile,
    order_policy: OrderPolicy,
    expected_account_id_hash: str | None,
) -> list[OrderValidationIssue]:
    payload = snapshot.payload
    account = _mapping(payload.get("account"))
    issues: list[OrderValidationIssue] = []
    if (
        risk_profile.environment
        != order_policy.environment
        or risk_profile.environment
        not in {"paper", "live"}
    ):
        issues.append(
            _issue(
                "POLICY_ENVIRONMENT_MISMATCH",
                "Stage F风险与订单policy环境不一致",
            )
        )
    if expected_account_id_hash is None:
        issues.append(
            _issue(
                "ACCOUNT_BINDING_MISSING",
                "缺少当前profile账户绑定hash",
                path="$.account.account_id_hash",
            )
        )
    elif (
        account.get("account_id_hash")
        != expected_account_id_hash
    ):
        issues.append(
            _issue(
                "ACCOUNT_HASH_MISMATCH",
                "pre-trade账户hash与profile绑定不匹配",
                path="$.account.account_id_hash",
            )
        )
    if account.get("status") != "ACTIVE":
        issues.append(
            _issue(
                "ACCOUNT_NOT_ACTIVE",
                "Alpaca账户不可交易",
                path="$.account.status",
            )
        )
    for field in (
        "trading_blocked",
        "account_blocked",
        "trade_suspended_by_user",
    ):
        if account.get(field) is True:
            issues.append(
                _issue(
                    "ACCOUNT_TRADING_BLOCKED",
                    f"账户安全字段为true：{field}",
                    path=f"$.account.{field}",
                )
            )
    if not snapshot.order_planning_ready:
        issues.append(
            _issue(
                "PRETRADE_SNAPSHOT_NOT_READY",
                "订单前关键刷新失败",
            )
        )
    execution_time = _parse_time(
        execution_output.get("generated_at")
    )
    snapshot_time = _parse_time(
        payload.get("retrieved_at")
    )
    if (
        execution_time is None
        or snapshot_time is None
        or snapshot_time <= execution_time
    ):
        issues.append(
            _issue(
                "PRETRADE_NOT_AFTER_EXECUTION",
                "pre-trade快照必须晚于execution output",
                path="$.retrieved_at",
            )
        )
    if (
        canonical_hash(execution_output)
        != plan.execution_output_hash
    ):
        issues.append(
            _issue(
                "EXECUTION_OUTPUT_HASH_MISMATCH",
                "execution output hash不匹配",
            )
        )
    if snapshot.snapshot_hash != plan.pretrade_snapshot_hash:
        issues.append(
            _issue(
                "PRETRADE_SNAPSHOT_HASH_MISMATCH",
                "pre-trade snapshot hash不匹配",
            )
        )
    identity_fields = (
        ("profile_id", plan.profile_id),
        ("strategy_id", plan.strategy_id),
        ("strategy_version", plan.strategy_version),
        ("run_date", plan.run_date),
        ("cycle_id", plan.cycle_id),
    )
    for field, expected in identity_fields:
        if (
            payload.get(field) != expected
            or execution_output.get(field) != expected
        ):
            issues.append(
                _issue(
                    "ORDER_IDENTITY_MISMATCH",
                    f"{field}在计划、快照和execution间不一致",
                    path=f"$.{field}",
                )
            )
    if (
        plan.risk_profile != risk_profile.reference
        or plan.order_policy != order_policy.reference
        or payload.get("order_policy")
        != order_policy.reference
    ):
        issues.append(
            _issue(
                "ORDER_CONFIG_VERSION_MISMATCH",
                "risk或order policy版本不一致",
            )
        )
    if (
        sha256_file(risk_profile.source_path)
        == ""
        or sha256_file(order_policy.source_path)
        == ""
    ):
        issues.append(
            _issue(
                "ORDER_CONFIG_HASH_INVALID",
                "risk或order policy hash无效",
            )
        )
    if execution_output.get("requires_manual_review") is True:
        issues.append(
            _issue(
                "EXECUTION_REQUIRES_MANUAL_REVIEW",
                "execution要求人工复核",
            )
        )
    phase = str(payload.get("market_phase", ""))
    if (
        phase in CLOSED_PHASES
        and order_policy.environment == "live"
    ):
        assets = _mapping(payload.get("assets"))
        closed_policy = _mapping(
            order_policy.settings.get(
                "closed_session_queue"
            )
        )
        for decision in _records(
            execution_output.get("decisions")
        ):
            if decision.get(
                "execution_decision"
            ) not in {"approve", "modify"}:
                continue
            symbol = str(
                decision.get("symbol", "")
            ).upper()
            if _mapping(
                assets.get(symbol)
            ).get("asset_class") == "crypto":
                continue
            action = str(
                decision.get(
                    "portfolio_action",
                    "",
                )
            )
            fraction_limit = decimal_or_zero(
                closed_policy.get(
                    "maximum_open_execution_fraction"
                    if action
                    in {"open", "increase"}
                    else "maximum_reduce_execution_fraction"
                )
            )
            fraction = decimal_or_zero(
                decision.get(
                    "execution_fraction"
                )
            )
            intent = _mapping(
                decision.get("order_intent")
            )
            if (
                fraction > fraction_limit
                or intent.get("preferred_type")
                not in set(
                    closed_policy.get(
                        "supported_order_types",
                        [],
                    )
                )
                or intent.get(
                    "time_in_force_preference"
                )
                not in set(
                    closed_policy.get(
                        "supported_time_in_force",
                        [],
                    )
                )
                or intent.get(
                    "extended_hours_requested"
                )
                is not False
                or intent.get("allow_queue")
                is not True
            ):
                issues.append(
                    _issue(
                        "CLOSED_SESSION_EXECUTION_INVALID",
                        "闭市排队意图超过保守比例或订单组合无效",
                    )
                )
    return issues


def _order_checks(
    order: ProposedOrder,
    *,
    snapshot: Mapping[str, Any],
    risk_profile: RiskProfile,
    order_policy: OrderPolicy,
    seen_clients: set[str],
    seen_plans: set[str],
) -> list[OrderValidationIssue]:
    issues: list[OrderValidationIssue] = []
    plan_id = order.plan_id
    if order.plan_id in seen_plans:
        issues.append(
            _issue(
                "DUPLICATE_PLAN_ID",
                "plan_id重复",
                plan_id=plan_id,
            )
        )
    seen_plans.add(order.plan_id)
    if order.client_order_id in seen_clients:
        issues.append(
            _issue(
                "DUPLICATE_CLIENT_ORDER_ID",
                "client_order_id重复",
                plan_id=plan_id,
            )
        )
    seen_clients.add(order.client_order_id)

    max_client_length = int(
        order_policy.settings.get(
            "client_order_id_max_length",
            48,
        )
    )
    if (
        not order.client_order_id.startswith("wa2-")
        or len(order.client_order_id)
        > max_client_length
    ):
        issues.append(
            _issue(
                "CLIENT_ORDER_ID_INVALID",
                "client_order_id字符、前缀或长度无效",
                plan_id=plan_id,
            )
        )
    if order.quantity <= ZERO:
        issues.append(
            _issue(
                "ORDER_QUANTITY_NOT_POSITIVE",
                "订单数量必须大于0",
                plan_id=plan_id,
            )
        )
    assets = _mapping(snapshot.get("assets"))
    asset = _mapping(assets.get(order.symbol))
    is_crypto = (
        asset.get("asset_class") == "crypto"
    )
    is_protective = (
        order.protection_role != "none"
    )
    precision = int(
        order_policy.settings.get(
            "fractional_quantity_precision",
            6,
        )
    )
    if (
        not order.fractionable
        and order.quantity
        != order.quantity.to_integral_value()
    ) or (
        order.fractionable
        and not is_crypto
        and not _precision_valid(
            order.quantity,
            precision,
        )
    ):
        issues.append(
            _issue(
                "ORDER_QUANTITY_PRECISION_INVALID",
                "数量与fractionable或精度不匹配",
                plan_id=plan_id,
            )
        )
    settings = risk_profile.settings
    minimum_value = decimal_or_zero(
        settings.get("minimum_order_value")
    )
    if order.planned_value < minimum_value:
        issues.append(
            _issue(
                "ORDER_BELOW_MINIMUM_VALUE",
                "订单价值低于最小值",
                plan_id=plan_id,
            )
        )
    account = _mapping(snapshot.get("account"))
    portfolio_value = decimal_or_zero(
        account.get("portfolio_value")
    )
    maximum_symbol = portfolio_value * decimal_or_zero(
        settings.get(
            "maximum_single_position_weight"
        )
    )
    if (
        order.side == "buy"
        and order.potential_position_value
        + order.planned_value
        > maximum_symbol
    ):
        issues.append(
            _issue(
                "MAXIMUM_SYMBOL_WEIGHT_EXCEEDED",
                "订单超过单标的上限",
                plan_id=plan_id,
            )
        )
    positions = {
        str(item.get("symbol", "")).upper(): item
        for item in _records(snapshot.get("positions"))
    }
    position = _mapping(
        positions.get(order.symbol)
    )
    crypto_policy = _mapping(
        order_policy.settings.get("crypto")
    )
    automatic_crypto_liquidation = (
        is_automatic_crypto_liquidation_order(
            side=order.side,
            order_type=order.order_type,
            time_in_force=order.time_in_force,
            extended_hours=order.extended_hours,
            asset=asset,
            policy=crypto_policy,
            position_exists=bool(position),
        )
    )
    if order.side == "sell":
        available = decimal_or_zero(
            position.get("available_quantity")
        )
        if order.quantity > available:
            issues.append(
                _issue(
                    "SELL_EXCEEDS_AVAILABLE_QUANTITY",
                    "卖出数量超过available quantity",
                    plan_id=plan_id,
                )
            )
        if (
            settings.get("allow_short_positions")
            is not True
            and not position
        ):
            issues.append(
                _issue(
                    "SHORT_POSITION_FORBIDDEN",
                    "默认禁止做空",
                    plan_id=plan_id,
                )
            )

    if asset.get("status") != "active":
        issues.append(
            _issue(
                "ASSET_NOT_ACTIVE",
                "资产状态不是active",
                plan_id=plan_id,
            )
        )
    if asset.get("tradable") is not True:
        issues.append(
            _issue(
                "ASSET_NOT_TRADABLE",
                "资产不可交易",
                plan_id=plan_id,
            )
        )
    if (
        str(asset.get("asset_class", ""))
        not in set(
            order_policy.settings.get(
                "supported_asset_classes",
                [],
            )
        )
    ):
        issues.append(
            _issue(
                "ASSET_CLASS_UNSUPPORTED",
                "资产类别不受order policy支持",
                plan_id=plan_id,
            )
        )

    quotes = _mapping(snapshot.get("quotes"))
    quote = _mapping(quotes.get(order.symbol))
    phase = str(
        snapshot.get("market_phase", "unknown")
    )
    if (
        phase in {"overnight", "overnight_session"}
        and not is_crypto
        and not is_protective
        and (
            asset.get("overnight_tradable") is not True
            or asset.get("overnight_halted") is True
        )
    ):
        issues.append(
            _issue(
                "ASSET_NOT_OVERNIGHT_TRADABLE",
                "资产不支持overnight或当前已暂停",
                plan_id=plan_id,
            )
        )
    if (
        phase not in VALID_MARKET_PHASES
        and not is_crypto
    ):
        issues.append(
            _issue(
                "MARKET_PHASE_INVALID",
                "市场阶段不在允许集合",
                plan_id=plan_id,
            )
        )
    if phase == "unknown" and not is_crypto:
        issues.append(
            _issue(
                "UNKNOWN_MARKET_NOT_APPROVABLE",
                "unknown市场阶段永远不得批准",
                plan_id=plan_id,
            )
        )
    age = decimal_or_zero(
        quote.get("quote_age_seconds")
    )
    max_age = decimal_or_zero(
        settings.get(
            "closed_session_quote_max_age_seconds"
            if (
                phase in CLOSED_PHASES
                and not is_crypto
            )
            else "quote_max_age_seconds"
        )
    )
    if (
        not automatic_crypto_liquidation
        and (
            quote.get("status") != "success"
            or quote.get("quote_age_seconds")
            is None
            or age > max_age
        )
    ):
        issues.append(
            _issue(
                "QUOTE_STALE_OR_MISSING",
                "最新报价缺失或过期",
                plan_id=plan_id,
            )
        )
    spread = decimal_or_zero(
        quote.get("spread_bps")
    )
    spread_limit = decimal_or_zero(
        settings.get(
            "extended_spread_limit_bps"
            if (
                phase in EXTENDED_PHASES
                and not is_crypto
            )
            else "regular_spread_limit_bps"
        )
    )
    if (
        not automatic_crypto_liquidation
        and (
            quote.get("spread_bps") is None
            or spread > spread_limit
        )
    ):
        issues.append(
            _issue(
                "SPREAD_LIMIT_EXCEEDED",
                "报价点差超过风险阈值",
                plan_id=plan_id,
            )
        )

    supported_types = _mapping(
        order_policy.settings.get(
            "supported_order_types"
        )
    )
    if is_protective:
        protection = _mapping(
            order_policy.settings.get(
                "protective_orders"
            )
        )
        allowed_classes = set(
            protection.get(
                "supported_order_classes",
                [],
            )
        )
        simple_types = set(
            protection.get(
                "supported_simple_types",
                [],
            )
        )
        fractional_types = set(
            protection.get(
                "fractional_supported_types",
                [],
            )
        )
        fractional = (
            order.quantity
            != order.quantity.to_integral_value()
        )
        if (
            protection.get("enabled") is not True
            or is_crypto
        ):
            issues.append(
                _issue(
                    "PROTECTIVE_ORDER_NOT_AUTHORIZED",
                    "当前order policy未授权该保护单",
                    plan_id=plan_id,
                )
            )
        if order.extended_hours:
            issues.append(
                _issue(
                    "PROTECTIVE_EXTENDED_HOURS_FORBIDDEN",
                    "Alpaca高级和保护单不得启用extended_hours",
                    plan_id=plan_id,
                )
            )
        if order.order_class not in allowed_classes:
            issues.append(
                _issue(
                    "PROTECTIVE_ORDER_CLASS_UNSUPPORTED",
                    "保护单order_class不受policy支持",
                    plan_id=plan_id,
                )
            )
        if (
            order.time_in_force
            not in set(
                order_policy.settings.get(
                    "supported_time_in_force",
                    [],
                )
            )
        ):
            issues.append(
                _issue(
                    "PROTECTIVE_TIME_IN_FORCE_UNSUPPORTED",
                    "保护单TIF不受policy支持",
                    plan_id=plan_id,
                )
            )
        if fractional and (
            order.order_class != "simple"
            or order.order_type
            not in fractional_types
            or order.time_in_force
            not in set(
                protection.get(
                    "fractional_time_in_force",
                    [],
                )
            )
        ):
            issues.append(
                _issue(
                    "FRACTIONAL_PROTECTION_COMBINATION_INVALID",
                    "碎股保护必须降级为券商允许的simple/day组合",
                    plan_id=plan_id,
                )
            )
        nested_take_profit = (
            order.take_profit_limit_price
        )
        nested_stop = order.stop_loss_stop_price
        if order.order_class == "simple":
            if order.order_type not in simple_types:
                issues.append(
                    _issue(
                        "PROTECTIVE_SIMPLE_TYPE_UNSUPPORTED",
                        "simple保护单类型不受policy支持",
                        plan_id=plan_id,
                    )
                )
            if (
                nested_take_profit is not None
                or nested_stop is not None
                or order.stop_loss_limit_price
                is not None
            ):
                issues.append(
                    _issue(
                        "SIMPLE_ORDER_HAS_NESTED_LEGS",
                        "simple保护单不得携带高级订单legs",
                        plan_id=plan_id,
                    )
                )
        elif order.order_class == "bracket":
            if (
                order.side != "buy"
                or order.order_type
                not in {"market", "limit"}
                or nested_take_profit is None
                or nested_stop is None
            ):
                issues.append(
                    _issue(
                        "BRACKET_COMBINATION_INVALID",
                        "bracket必须是带止盈和止损的新入场买单",
                        plan_id=plan_id,
                    )
                )
        elif order.order_class == "oco":
            if (
                order.side != "sell"
                or order.order_type != "limit"
                or order.limit_price is None
                or nested_take_profit is None
                or nested_stop is None
            ):
                issues.append(
                    _issue(
                        "OCO_COMBINATION_INVALID",
                        "OCO必须是现有持仓的止盈加止损卖出组合",
                        plan_id=plan_id,
                    )
                )
        elif order.order_class == "oto":
            if (
                order.side != "buy"
                or order.order_type
                not in {"market", "limit"}
                or (
                    (nested_take_profit is None)
                    == (nested_stop is None)
                )
            ):
                issues.append(
                    _issue(
                        "OTO_COMBINATION_INVALID",
                        "OTO新入场必须且只能携带一个止盈或止损leg",
                        plan_id=plan_id,
                    )
                )
        if order.order_type == "stop" and (
            order.stop_price is None
            or order.stop_price <= ZERO
        ):
            issues.append(
                _issue(
                    "STOP_PRICE_INVALID",
                    "stop订单必须有正stop_price",
                    plan_id=plan_id,
                )
            )
        if order.order_type == "stop_limit" and (
            order.stop_price is None
            or order.stop_price <= ZERO
            or order.limit_price is None
            or order.limit_price <= ZERO
            or order.limit_price > order.stop_price
        ):
            issues.append(
                _issue(
                    "STOP_LIMIT_PRICES_INVALID",
                    "卖出stop-limit必须满足0 < limit <= stop",
                    plan_id=plan_id,
                )
            )
        if order.order_type == "trailing_stop" and (
            order.order_class != "simple"
            or (
                (order.trail_price is None)
                == (order.trail_percent is None)
            )
        ):
            issues.append(
                _issue(
                    "TRAILING_STOP_COMBINATION_INVALID",
                    "移动止损必须是simple且只能设置trail_price或trail_percent之一",
                    plan_id=plan_id,
                )
            )
        if (
            order.side == "sell"
            and order.order_type == "limit"
            and order.limit_price is not None
            and order.limit_price
            <= order.reference_price
        ):
            issues.append(
                _issue(
                    "TAKE_PROFIT_NOT_ABOVE_REFERENCE",
                    "多头止盈卖价必须高于当前参考价",
                    plan_id=plan_id,
                )
            )
        if (
            order.stop_price is not None
            and order.stop_price
            >= order.reference_price
        ) or (
            nested_stop is not None
            and nested_stop
            >= order.reference_price
        ):
            issues.append(
                _issue(
                    "STOP_NOT_BELOW_REFERENCE",
                    "多头止损触发价必须低于当前参考价",
                    plan_id=plan_id,
                )
            )
        if (
            order.stop_loss_limit_price
            is not None
            and nested_stop is not None
            and order.stop_loss_limit_price
            > nested_stop
        ):
            issues.append(
                _issue(
                    "NESTED_STOP_LIMIT_RELATION_INVALID",
                    "卖出保护leg的limit不得高于stop",
                    plan_id=plan_id,
                )
            )
    elif is_crypto:
        minimum_quantity = decimal_or_zero(
            asset.get("min_order_size")
        )
        trade_increment = decimal_or_zero(
            asset.get("min_trade_increment")
        )
        price_increment = decimal_or_zero(
            asset.get("price_increment")
        )
        position_action_valid = (
            order.side == "sell"
            and bool(position)
        )
        if (
            minimum_quantity <= ZERO
            or trade_increment <= ZERO
            or (
                order.order_type == "limit"
                and price_increment <= ZERO
            )
        ):
            issues.append(
                _issue(
                    "CRYPTO_ASSET_INCREMENT_MISSING",
                    "加密资产缺少券商数量或价格步进",
                    plan_id=plan_id,
                )
            )
        if (
            minimum_quantity > ZERO
            and order.quantity < minimum_quantity
        ):
            issues.append(
                _issue(
                    "CRYPTO_MINIMUM_ORDER_SIZE_INVALID",
                    "加密订单数量低于资产最小订单数量",
                    plan_id=plan_id,
                )
            )
        if (
            trade_increment > ZERO
            and order.quantity % trade_increment
            != ZERO
        ):
            issues.append(
                _issue(
                    "CRYPTO_QUANTITY_INCREMENT_INVALID",
                    "加密订单数量不符合资产交易步进",
                    plan_id=plan_id,
                )
            )
        if (
            order.limit_price is not None
            and price_increment > ZERO
            and order.limit_price % price_increment
            != ZERO
        ):
            issues.append(
                _issue(
                    "CRYPTO_PRICE_INCREMENT_INVALID",
                    "加密限价不符合资产价格步进",
                    plan_id=plan_id,
                )
            )
        if (
            order.order_type
            not in set(
                supported_types.get("crypto", [])
            )
            or order.time_in_force
            not in set(
                crypto_policy.get(
                    "supported_time_in_force",
                    [],
                )
            )
            or order.extended_hours
            or not position_action_valid
            or (
                order.side == "buy"
                and not position
                and crypto_policy.get(
                    "allow_new_positions"
                )
                is not True
            )
        ):
            issues.append(
                _issue(
                    "CRYPTO_ORDER_COMBINATION_INVALID",
                    "加密订单类型、TIF、持仓方向或extended_hours无效",
                    plan_id=plan_id,
                )
            )
    elif phase == "regular_session":
        if order.order_type not in set(
            supported_types.get(
                "regular_session",
                [],
            )
        ):
            issues.append(
                _issue(
                    "REGULAR_ORDER_TYPE_UNSUPPORTED",
                    "regular session订单类型不支持",
                    plan_id=plan_id,
                )
            )
        if order.time_in_force not in set(
            order_policy.settings.get(
                "supported_time_in_force",
                [],
            )
        ):
            issues.append(
                _issue(
                    "REGULAR_TIME_IN_FORCE_UNSUPPORTED",
                    "regular session TIF不受order policy支持",
                    plan_id=plan_id,
                )
            )
        if order.extended_hours:
            issues.append(
                _issue(
                    "REGULAR_ORDER_MARKED_EXTENDED",
                    "regular session不得标记extended_hours",
                    plan_id=plan_id,
                )
            )
    elif phase in EXTENDED_PHASES:
        extended_policy = _mapping(
            order_policy.settings.get(
                "extended_hours"
            )
        )
        capabilities = _mapping(
            snapshot.get("broker_capabilities")
        )
        if (
            order.order_type != "limit"
            or order.limit_price is None
            or not order.extended_hours
            or order.time_in_force
            not in set(
                extended_policy.get(
                    "supported_time_in_force",
                    [],
                )
            )
            or capabilities.get(
                "supports_extended_hours"
            )
            is not True
        ):
            issues.append(
                _issue(
                    "EXTENDED_ORDER_COMBINATION_INVALID",
                    "扩展时段订单组合或券商能力无效",
                    plan_id=plan_id,
                )
            )
    elif phase in CLOSED_PHASES:
        queue = _mapping(
            order_policy.settings.get("queue_policy")
        )
        closed_policy = _mapping(
            order_policy.settings.get(
                "closed_session_queue"
            )
        )
        capabilities = _mapping(
            snapshot.get("broker_capabilities")
        )
        if not (
            queue.get(phase) is True
            and capabilities.get(
                "supports_closed_session_queue"
            )
            is True
            and order.order_type
            in set(
                closed_policy.get(
                    "supported_order_types",
                    [],
                )
            )
            and order.time_in_force
            in set(
                closed_policy.get(
                    "supported_time_in_force",
                    [],
                )
            )
            and not order.extended_hours
        ):
            issues.append(
                _issue(
                    "CLOSED_SESSION_QUEUE_UNSUPPORTED",
                    "闭市排队不受策略与券商共同支持",
                    plan_id=plan_id,
                )
            )
    if (
        order.order_type == "limit"
        and (
            order.limit_price is None
            or order.limit_price <= ZERO
        )
    ):
        issues.append(
            _issue(
                "LIMIT_PRICE_INVALID",
                "limit订单必须有正价格",
                plan_id=plan_id,
            )
        )
    price_precision = int(
        order_policy.settings.get(
            "price_precision",
            2,
        )
    )
    if (
        order.limit_price is not None
        and not is_crypto
        and not _precision_valid(
            order.limit_price,
            price_precision,
        )
    ):
        issues.append(
            _issue(
                "LIMIT_PRICE_PRECISION_INVALID",
                "limit price精度无效",
                plan_id=plan_id,
            )
        )
    for field, value in (
        ("stop_price", order.stop_price),
        ("trail_price", order.trail_price),
        (
            "take_profit_limit_price",
            order.take_profit_limit_price,
        ),
        (
            "stop_loss_stop_price",
            order.stop_loss_stop_price,
        ),
        (
            "stop_loss_limit_price",
            order.stop_loss_limit_price,
        ),
    ):
        if (
            value is not None
            and not is_crypto
            and not _precision_valid(
                value,
                price_precision,
            )
        ):
            issues.append(
                _issue(
                    "PROTECTIVE_PRICE_PRECISION_INVALID",
                    f"{field}精度无效",
                    plan_id=plan_id,
                )
            )
    boundary = order.price_condition.get(
        "do_not_execute_above"
    )
    if (
        order.side == "buy"
        and boundary is not None
        and order.reference_price
        > decimal_or_zero(boundary)
    ):
        issues.append(
            _issue(
                "DO_NOT_EXECUTE_BOUNDARY_BREACHED",
                "买入参考价越过do-not-execute边界",
                plan_id=plan_id,
            )
        )

    open_orders = [
        item
        for item in _records(
            snapshot.get("open_orders")
        )
        if str(item.get("status", "")).lower()
        in ACTIVE_ORDER_STATUSES
    ]
    same = [
        item
        for item in open_orders
        if str(item.get("symbol", "")).upper()
        == order.symbol
        and not is_system_protective_order(
            item
        )
        and str(item.get("side", "")).lower()
        == order.side
    ]
    opposite = [
        item
        for item in open_orders
        if str(item.get("symbol", "")).upper()
        == order.symbol
        and not is_system_protective_order(
            item
        )
        and str(item.get("side", "")).lower()
        not in {order.side, ""}
    ]
    if same:
        issues.append(
            _issue(
                "SAME_SIDE_OPEN_ORDER_EXISTS",
                "存在同方向有效挂单",
                plan_id=plan_id,
            )
        )
    if opposite:
        issues.append(
            _issue(
                "OPPOSITE_SIDE_OPEN_ORDER_EXISTS",
                "存在相反方向有效挂单",
                plan_id=plan_id,
            )
        )
    existing_clients = {
        str(item.get("client_order_id", ""))
        for item in [
            *_records(snapshot.get("open_orders")),
            *_records(snapshot.get("today_orders")),
        ]
    }
    if order.client_order_id in existing_clients:
        issues.append(
            _issue(
                "BROKER_CLIENT_ORDER_ID_EXISTS",
                "券商已存在相同client_order_id",
                plan_id=plan_id,
            )
        )
    if order.depends_on:
        issues.append(
            _issue(
                "ORDER_DEPENDENCY_UNRESOLVED",
                "replacement或冲突依赖尚未满足",
                plan_id=plan_id,
            )
        )
    return issues


def validate_order_plan(
    *,
    plan: ProposedOrderPlan,
    execution_output: Mapping[str, Any],
    pretrade_snapshot: PreTradeSnapshot | Mapping[str, Any],
    risk_profile: RiskProfile,
    order_policy: OrderPolicy,
    expected_account_id_hash: str | None = None,
    generated_at: str | None = None,
) -> ValidatedOrderPlan:
    """Apply global and per-order checks, then assign only local approval states."""

    snapshot = (
        pretrade_snapshot
        if isinstance(
            pretrade_snapshot,
            PreTradeSnapshot,
        )
        else PreTradeSnapshot.from_payload(
            pretrade_snapshot
        )
    )
    global_issues = _global_checks(
        plan=plan,
        execution_output=execution_output,
        snapshot=snapshot,
        risk_profile=risk_profile,
        order_policy=order_policy,
        expected_account_id_hash=(
            expected_account_id_hash
        ),
    )
    account = _mapping(
        snapshot.payload.get("account")
    )
    settings = risk_profile.settings
    buy_total = sum(
        (
            order.planned_value
            for order in plan.orders
            if order.side == "buy"
            and order.status
            == OrderStatus.PROPOSED
        ),
        ZERO,
    )
    cash = decimal_or_zero(account.get("cash"))
    buying_power = decimal_or_zero(
        account.get("buying_power")
    )
    portfolio_value = decimal_or_zero(
        account.get("portfolio_value")
    )
    minimum_cash = portfolio_value * decimal_or_zero(
        settings.get("minimum_cash_weight")
    )
    per_cycle = portfolio_value * decimal_or_zero(
        settings.get(
            "maximum_new_capital_per_cycle_weight"
        )
    )
    if buy_total > buying_power:
        global_issues.append(
            _issue(
                "BUYING_POWER_EXCEEDED",
                "买单合计超过buying power",
            )
        )
    if cash - buy_total < minimum_cash:
        global_issues.append(
            _issue(
                "MINIMUM_CASH_RESERVE_BREACHED",
                "买单合计突破最低现金储备",
            )
        )
    if buy_total > per_cycle:
        global_issues.append(
            _issue(
                "PER_CYCLE_DEPLOYMENT_EXCEEDED",
                "买单合计超过单轮部署上限",
            )
        )
    maximum_orders = int(
        settings.get("maximum_order_count", 0)
    )
    if sum(
        order.status == OrderStatus.PROPOSED
        for order in plan.orders
    ) > maximum_orders:
        global_issues.append(
            _issue(
                "MAXIMUM_ORDER_COUNT_EXCEEDED",
                "拟批准订单数超过上限",
            )
        )

    seen_clients: set[str] = set()
    seen_plans: set[str] = set()
    validated: list[ValidatedOrder] = []
    for order in plan.orders:
        if order.status != OrderStatus.PROPOSED:
            validated.append(
                ValidatedOrder(
                    order=order,
                    status=order.status,
                )
            )
            continue
        order_issues = _order_checks(
            order,
            snapshot=snapshot.payload,
            risk_profile=risk_profile,
            order_policy=order_policy,
            seen_clients=seen_clients,
            seen_plans=seen_plans,
        )
        if global_issues or order_issues:
            status = OrderStatus.BLOCKED
        else:
            status = (
                OrderStatus.APPROVED
                if plan.permission.submission_requested
                else OrderStatus.DRY_RUN_APPROVED
            )
        validated.append(
            ValidatedOrder(
                order=order,
                status=status,
                issues=tuple(order_issues),
            )
        )
    return ValidatedOrderPlan(
        proposed=plan,
        orders=tuple(validated),
        global_issues=tuple(global_issues),
        generated_at=generated_at or utc_now_iso(),
    )
