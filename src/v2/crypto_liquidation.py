"""定义 Live 账户既有 Crypto 自动清仓的确定性判定。

作用：识别由 Python 强制生成的 Crypto 全量卖出意图和对应市价订单。
重要性：股票只能使用 USD 资金；Crypto 不再交给模型决定持有，但仍必须经过账户、资产、数量、幂等和提交校验。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


ZERO = Decimal("0")


def _decimal_or_zero(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        return ZERO
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ZERO
    return result if result.is_finite() else ZERO


def automatic_crypto_liquidation_enabled(
    policy: Mapping[str, Any],
) -> bool:
    """Return whether the selected policy requires deterministic liquidation."""

    return (
        policy.get(
            "liquidate_existing_positions_on_detection"
        )
        is True
        or policy.get("enabled") is True
    )


def is_crypto_asset(
    asset: Mapping[str, Any],
) -> bool:
    return (
        str(asset.get("asset_class", "")).lower()
        == "crypto"
    )


def is_automatic_crypto_liquidation_decision(
    decision: Mapping[str, Any],
    asset: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> bool:
    """Recognize the exact hard-coded sell-all intent."""

    intent = decision.get("order_intent")
    intent = (
        intent
        if isinstance(intent, Mapping)
        else {}
    )
    return (
        automatic_crypto_liquidation_enabled(policy)
        and is_crypto_asset(asset)
        and decision.get("portfolio_action") == "close"
        and decision.get("execution_decision")
        in {"approve", "modify"}
        and decision.get("side") == "sell"
        and _decimal_or_zero(
            decision.get("target_weight")
        )
        == ZERO
        and _decimal_or_zero(
            decision.get("maximum_weight")
        )
        == ZERO
        and _decimal_or_zero(
            decision.get("execution_fraction")
        )
        == Decimal("1")
        and intent.get("preferred_type") == "market"
        and intent.get("time_in_force_preference")
        == "gtc"
        and intent.get("extended_hours_requested")
        is False
    )


def is_automatic_crypto_liquidation_order(
    *,
    side: str,
    order_type: str,
    time_in_force: str,
    extended_hours: bool,
    asset: Mapping[str, Any],
    policy: Mapping[str, Any],
    position_exists: bool,
) -> bool:
    """Recognize the Stage F order created from the sell-all intent."""

    return (
        automatic_crypto_liquidation_enabled(policy)
        and is_crypto_asset(asset)
        and position_exists
        and side == "sell"
        and order_type == "market"
        and time_in_force == "gtc"
        and not extended_hours
    )
