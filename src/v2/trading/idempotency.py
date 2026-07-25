"""生成 WA Trader v2 稳定且不泄露账户身份的订单幂等 ID。

作用：由 profile、strategy、cycle、symbol、side、意图序号、角色和算法版本生成 ID。
重要性：Stage G 将依赖 client_order_id 去重；算法稳定性直接防止恢复或重试时重复下单。
"""

from __future__ import annotations

import hashlib
import re


SAFE = re.compile(r"[^a-z0-9-]+")


def _component(value: object, *, limit: int) -> str:
    normalized = SAFE.sub(
        "-",
        str(value).strip().lower().replace("_", "-"),
    ).strip("-")
    return (normalized or "x")[:limit]


def build_plan_id(
    *,
    profile_id: str,
    strategy_id: str,
    strategy_version: str,
    cycle_id: str,
    symbol: str,
    side: str,
    intent_index: int,
    order_role: str,
    idempotency_version: str,
) -> str:
    raw = "|".join(
        (
            profile_id,
            strategy_id,
            strategy_version,
            cycle_id,
            symbol,
            side,
            str(intent_index),
            order_role,
            idempotency_version,
        )
    )
    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]
    return f"plan-{_component(symbol, limit=12)}-{digest}"


def build_client_order_id(
    *,
    profile_id: str,
    strategy_id: str,
    strategy_version: str,
    cycle_id: str,
    symbol: str,
    side: str,
    intent_index: int,
    order_role: str,
    idempotency_version: str,
    max_length: int = 48,
) -> str:
    """Return an Alpaca-safe deterministic ID within the configured limit."""

    plan_id = build_plan_id(
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        cycle_id=cycle_id,
        symbol=symbol,
        side=side,
        intent_index=intent_index,
        order_role=order_role,
        idempotency_version=idempotency_version,
    )
    digest = plan_id.rsplit("-", 1)[-1][:12]
    prefix = "-".join(
        (
            "wa2",
            _component(profile_id, limit=8),
            _component(cycle_id, limit=15),
            _component(side, limit=4),
            _component(symbol, limit=10),
        )
    )
    suffix = f"-{digest}"
    available = max(1, max_length - len(suffix))
    return f"{prefix[:available].rstrip('-')}{suffix}"[
        :max_length
    ]
