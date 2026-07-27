"""定义 WA Trader v2 Stage F 的订单规划与硬校验模型。

作用：用 Decimal 保存暴露、数量、价格和资本，并把 proposed、validated 与请求规格分层。
重要性：这是研究意图与未来真实提交之间的机器可审计合同；Stage F 只能规划，不能声称已成交。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping


ZERO = Decimal("0")


class OrderStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    DEPENDENT = "dependent"
    DRY_RUN_APPROVED = "dry_run_approved"


class OrderAction(StrEnum):
    SUBMIT = "submit"
    KEEP = "keep"
    CANCEL = "cancel"
    REPLACE = "replace"
    REVIEW = "review"
    NONE = "none"


def decimal_value(value: object, *, label: str = "value") -> Decimal:
    """Return a finite Decimal without accepting booleans or missing values."""

    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label}必须是有限十进制数")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label}必须是有限十进制数") from error
    if not result.is_finite():
        raise ValueError(f"{label}必须是有限十进制数")
    return result


def decimal_or_zero(value: object) -> Decimal:
    try:
        return decimal_value(value)
    except ValueError:
        return ZERO


def decimal_text(value: Decimal | object) -> str:
    """Serialize a decimal without exponent notation or binary floats."""

    result = value if isinstance(value, Decimal) else decimal_value(value)
    normalized = format(result, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SubmissionPermission:
    submission_requested: bool
    dry_run: bool
    submission_performed: bool = False
    submitted_order_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_requested": self.submission_requested,
            "dry_run": self.dry_run,
            "submission_performed": self.submission_performed,
            "submitted_order_count": self.submitted_order_count,
        }


@dataclass(frozen=True)
class PreTradeSnapshot:
    payload: Mapping[str, Any]
    snapshot_hash: str
    order_planning_ready: bool

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PreTradeSnapshot":
        copied = dict(payload)
        return cls(
            payload=copied,
            snapshot_hash=canonical_hash(copied),
            order_planning_ready=bool(
                copied.get("order_planning_ready")
            ),
        )


@dataclass(frozen=True)
class ProposedOrderAction:
    action_id: str
    order_reference: str
    symbol: str
    action: OrderAction
    status: OrderStatus
    reason: str
    broker_order_id: str | None = None
    client_order_id: str | None = None
    depends_on: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ProposedOrderAction":
        return cls(
            action_id=str(payload["action_id"]),
            order_reference=str(
                payload.get("order_reference", "")
            ),
            symbol=str(payload.get("symbol", "")),
            action=OrderAction(
                str(payload.get("action", "review"))
            ),
            status=OrderStatus(
                str(payload.get("status", "blocked"))
            ),
            reason=str(payload.get("reason", "")),
            broker_order_id=(
                str(payload["broker_order_id"])
                if payload.get("broker_order_id")
                is not None
                else None
            ),
            client_order_id=(
                str(payload["client_order_id"])
                if payload.get("client_order_id")
                is not None
                else None
            ),
            depends_on=tuple(
                str(item)
                for item in payload.get(
                    "depends_on",
                    [],
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "order_reference": self.order_reference,
            "symbol": self.symbol,
            "action": self.action.value,
            "status": self.status.value,
            "reason": self.reason,
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class ProposedOrder:
    plan_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    planned_value: Decimal
    reference_price: Decimal
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool
    client_order_id: str
    status: OrderStatus
    reason_codes: tuple[str, ...]
    current_position_value: Decimal
    open_buy_remaining_value: Decimal
    open_sell_remaining_value: Decimal
    potential_position_value: Decimal
    target_position_value: Decimal
    raw_delta_value: Decimal
    execution_delta_value: Decimal
    sector: str = "unknown"
    fractionable: bool = False
    market_phase: str = "unknown"
    urgency: str = "none"
    priority: int = 999999
    conviction: str = "none"
    depends_on: tuple[str, ...] = ()
    price_condition: Mapping[str, Any] = field(default_factory=dict)
    order_class: str = "simple"
    stop_price: Decimal | None = None
    trail_price: Decimal | None = None
    trail_percent: Decimal | None = None
    take_profit_limit_price: Decimal | None = None
    stop_loss_stop_price: Decimal | None = None
    stop_loss_limit_price: Decimal | None = None
    protection_role: str = "none"

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ProposedOrder":
        exposure = payload.get("exposure")
        exposure = (
            exposure
            if isinstance(exposure, Mapping)
            else {}
        )
        return cls(
            plan_id=str(payload["plan_id"]),
            symbol=str(payload.get("symbol", "")),
            side=str(payload.get("side", "none")),
            order_type=str(
                payload.get("order_type", "none")
            ),
            quantity=decimal_value(
                payload.get("quantity", "0")
            ),
            planned_value=decimal_value(
                payload.get("planned_value", "0")
            ),
            reference_price=decimal_value(
                payload.get("reference_price", "0")
            ),
            limit_price=(
                decimal_value(payload["limit_price"])
                if payload.get("limit_price")
                is not None
                else None
            ),
            time_in_force=str(
                payload.get("time_in_force", "none")
            ),
            extended_hours=bool(
                payload.get("extended_hours")
            ),
            client_order_id=str(
                payload.get("client_order_id", "")
            ),
            status=OrderStatus(
                str(payload.get("status", "blocked"))
            ),
            reason_codes=tuple(
                str(item)
                for item in payload.get(
                    "reason_codes",
                    [],
                )
            ),
            current_position_value=decimal_value(
                exposure.get(
                    "current_position_value",
                    "0",
                )
            ),
            open_buy_remaining_value=decimal_value(
                exposure.get(
                    "open_buy_remaining_value",
                    "0",
                )
            ),
            open_sell_remaining_value=decimal_value(
                exposure.get(
                    "open_sell_remaining_value",
                    "0",
                )
            ),
            potential_position_value=decimal_value(
                exposure.get(
                    "potential_position_value",
                    "0",
                )
            ),
            target_position_value=decimal_value(
                exposure.get(
                    "target_position_value",
                    "0",
                )
            ),
            raw_delta_value=decimal_value(
                exposure.get(
                    "raw_delta_value",
                    "0",
                )
            ),
            execution_delta_value=decimal_value(
                exposure.get(
                    "execution_delta_value",
                    "0",
                )
            ),
            sector=str(
                payload.get("sector", "unknown")
            ),
            fractionable=bool(
                payload.get("fractionable")
            ),
            market_phase=str(
                payload.get(
                    "market_phase",
                    "unknown",
                )
            ),
            urgency=str(
                payload.get("urgency", "none")
            ),
            priority=int(
                payload.get("priority", 999999)
            ),
            conviction=str(
                payload.get("conviction", "none")
            ),
            depends_on=tuple(
                str(item)
                for item in payload.get(
                    "depends_on",
                    [],
                )
            ),
            price_condition=(
                dict(payload["price_condition"])
                if isinstance(
                    payload.get("price_condition"),
                    Mapping,
                )
                else {}
            ),
            order_class=str(
                payload.get("order_class", "simple")
            ),
            stop_price=(
                decimal_value(payload["stop_price"])
                if payload.get("stop_price")
                is not None
                else None
            ),
            trail_price=(
                decimal_value(payload["trail_price"])
                if payload.get("trail_price")
                is not None
                else None
            ),
            trail_percent=(
                decimal_value(
                    payload["trail_percent"]
                )
                if payload.get("trail_percent")
                is not None
                else None
            ),
            take_profit_limit_price=(
                decimal_value(
                    payload[
                        "take_profit_limit_price"
                    ]
                )
                if payload.get(
                    "take_profit_limit_price"
                )
                is not None
                else None
            ),
            stop_loss_stop_price=(
                decimal_value(
                    payload["stop_loss_stop_price"]
                )
                if payload.get(
                    "stop_loss_stop_price"
                )
                is not None
                else None
            ),
            stop_loss_limit_price=(
                decimal_value(
                    payload["stop_loss_limit_price"]
                )
                if payload.get(
                    "stop_loss_limit_price"
                )
                is not None
                else None
            ),
            protection_role=str(
                payload.get(
                    "protection_role",
                    "none",
                )
            ),
        )

    def with_status(
        self,
        status: OrderStatus,
        *reason_codes: str,
    ) -> "ProposedOrder":
        return replace(
            self,
            status=status,
            reason_codes=tuple(
                dict.fromkeys(
                    (*self.reason_codes, *reason_codes)
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": decimal_text(self.quantity),
            "planned_value": decimal_text(self.planned_value),
            "reference_price": decimal_text(self.reference_price),
            "limit_price": (
                decimal_text(self.limit_price)
                if self.limit_price is not None
                else None
            ),
            "time_in_force": self.time_in_force,
            "extended_hours": self.extended_hours,
            "client_order_id": self.client_order_id,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "exposure": {
                "current_position_value": decimal_text(
                    self.current_position_value
                ),
                "open_buy_remaining_value": decimal_text(
                    self.open_buy_remaining_value
                ),
                "open_sell_remaining_value": decimal_text(
                    self.open_sell_remaining_value
                ),
                "potential_position_value": decimal_text(
                    self.potential_position_value
                ),
                "target_position_value": decimal_text(
                    self.target_position_value
                ),
                "raw_delta_value": decimal_text(
                    self.raw_delta_value
                ),
                "execution_delta_value": decimal_text(
                    self.execution_delta_value
                ),
            },
            "sector": self.sector,
            "fractionable": self.fractionable,
            "market_phase": self.market_phase,
            "urgency": self.urgency,
            "priority": self.priority,
            "conviction": self.conviction,
            "depends_on": list(self.depends_on),
            "price_condition": dict(self.price_condition),
            "order_class": self.order_class,
            "stop_price": (
                decimal_text(self.stop_price)
                if self.stop_price is not None
                else None
            ),
            "trail_price": (
                decimal_text(self.trail_price)
                if self.trail_price is not None
                else None
            ),
            "trail_percent": (
                decimal_text(self.trail_percent)
                if self.trail_percent is not None
                else None
            ),
            "take_profit_limit_price": (
                decimal_text(
                    self.take_profit_limit_price
                )
                if self.take_profit_limit_price
                is not None
                else None
            ),
            "stop_loss_stop_price": (
                decimal_text(
                    self.stop_loss_stop_price
                )
                if self.stop_loss_stop_price
                is not None
                else None
            ),
            "stop_loss_limit_price": (
                decimal_text(
                    self.stop_loss_limit_price
                )
                if self.stop_loss_limit_price
                is not None
                else None
            ),
            "protection_role": self.protection_role,
        }


@dataclass(frozen=True)
class OrderPlanSummary:
    proposed: int = 0
    approved: int = 0
    dry_run_approved: int = 0
    blocked: int = 0
    skipped: int = 0
    dependent: int = 0
    estimated_buy_value: Decimal = ZERO
    estimated_sell_value: Decimal = ZERO
    submission_performed: bool = False
    submitted_order_count: int = 0

    @classmethod
    def from_orders(
        cls,
        orders: tuple[ProposedOrder, ...],
    ) -> "OrderPlanSummary":
        counts = {
            status.value: sum(
                order.status == status
                for order in orders
            )
            for status in OrderStatus
        }
        return cls(
            proposed=len(orders),
            approved=counts["approved"],
            dry_run_approved=counts[
                "dry_run_approved"
            ],
            blocked=counts["blocked"],
            skipped=counts["skipped"],
            dependent=counts["dependent"],
            estimated_buy_value=sum(
                (
                    order.planned_value
                    for order in orders
                    if order.side == "buy"
                    and order.status
                    not in {
                        OrderStatus.BLOCKED,
                        OrderStatus.SKIPPED,
                    }
                ),
                ZERO,
            ),
            estimated_sell_value=sum(
                (
                    order.planned_value
                    for order in orders
                    if order.side == "sell"
                    and order.status
                    not in {
                        OrderStatus.BLOCKED,
                        OrderStatus.SKIPPED,
                    }
                ),
                ZERO,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed": self.proposed,
            "approved": self.approved,
            "dry_run_approved": self.dry_run_approved,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "dependent": self.dependent,
            "estimated_buy_value": decimal_text(
                self.estimated_buy_value
            ),
            "estimated_sell_value": decimal_text(
                self.estimated_sell_value
            ),
            "submission_performed": self.submission_performed,
            "submitted_order_count": self.submitted_order_count,
        }


@dataclass(frozen=True)
class ProposedOrderPlan:
    profile_id: str
    strategy_id: str
    strategy_version: str
    risk_profile: str
    order_policy: str
    run_date: str
    cycle_id: str
    generated_at: str
    execution_output_hash: str
    pretrade_snapshot_hash: str
    permission: SubmissionPermission
    orders: tuple[ProposedOrder, ...]
    actions: tuple[ProposedOrderAction, ...]
    warnings: tuple[str, ...] = ()
    global_issues: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ProposedOrderPlan":
        return cls(
            profile_id=str(payload["profile_id"]),
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(
                payload["strategy_version"]
            ),
            risk_profile=str(
                payload["risk_profile"]
            ),
            order_policy=str(
                payload["order_policy"]
            ),
            run_date=str(payload["run_date"]),
            cycle_id=str(payload["cycle_id"]),
            generated_at=str(
                payload["generated_at"]
            ),
            execution_output_hash=str(
                payload["execution_output_hash"]
            ),
            pretrade_snapshot_hash=str(
                payload["pretrade_snapshot_hash"]
            ),
            permission=SubmissionPermission(
                submission_requested=bool(
                    payload.get(
                        "submission_requested"
                    )
                ),
                dry_run=bool(
                    payload.get("dry_run")
                ),
                submission_performed=bool(
                    payload.get(
                        "submission_performed"
                    )
                ),
                submitted_order_count=int(
                    payload.get(
                        "submitted_order_count",
                        0,
                    )
                ),
            ),
            orders=tuple(
                ProposedOrder.from_dict(item)
                for item in payload.get(
                    "orders",
                    [],
                )
                if isinstance(item, Mapping)
            ),
            actions=tuple(
                ProposedOrderAction.from_dict(item)
                for item in payload.get(
                    "actions",
                    [],
                )
                if isinstance(item, Mapping)
            ),
            warnings=tuple(
                str(item)
                for item in payload.get(
                    "warnings",
                    [],
                )
            ),
            global_issues=tuple(
                str(item)
                for item in payload.get(
                    "global_issues",
                    [],
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "risk_profile": self.risk_profile,
            "order_policy": self.order_policy,
            "run_date": self.run_date,
            "cycle_id": self.cycle_id,
            "generated_at": self.generated_at,
            "execution_output_hash": self.execution_output_hash,
            "pretrade_snapshot_hash": self.pretrade_snapshot_hash,
            **self.permission.to_dict(),
            "orders": [
                order.to_dict() for order in self.orders
            ],
            "actions": [
                action.to_dict() for action in self.actions
            ],
            "summary": OrderPlanSummary.from_orders(
                self.orders
            ).to_dict(),
            "warnings": list(self.warnings),
            "global_issues": list(self.global_issues),
        }


@dataclass(frozen=True)
class OrderValidationIssue:
    code: str
    message: str
    severity: str = "error"
    path: str = "$"
    plan_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
            "plan_id": self.plan_id,
        }


@dataclass(frozen=True)
class ValidatedOrder:
    order: ProposedOrder
    status: OrderStatus
    issues: tuple[OrderValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = self.order.with_status(
            self.status
        ).to_dict()
        payload["validation_issues"] = [
            issue.to_dict() for issue in self.issues
        ]
        return payload


@dataclass(frozen=True)
class ValidatedOrderPlan:
    proposed: ProposedOrderPlan
    orders: tuple[ValidatedOrder, ...]
    global_issues: tuple[OrderValidationIssue, ...]
    generated_at: str

    @property
    def normalized_orders(self) -> tuple[ProposedOrder, ...]:
        return tuple(
            item.order.with_status(item.status)
            for item in self.orders
        )

    def to_dict(self) -> dict[str, Any]:
        summary = OrderPlanSummary.from_orders(
            self.normalized_orders
        )
        return {
            "schema_version": "1.0",
            "profile_id": self.proposed.profile_id,
            "strategy_id": self.proposed.strategy_id,
            "strategy_version": self.proposed.strategy_version,
            "risk_profile": self.proposed.risk_profile,
            "order_policy": self.proposed.order_policy,
            "run_date": self.proposed.run_date,
            "cycle_id": self.proposed.cycle_id,
            "generated_at": self.generated_at,
            "execution_output_hash": (
                self.proposed.execution_output_hash
            ),
            "pretrade_snapshot_hash": (
                self.proposed.pretrade_snapshot_hash
            ),
            **self.proposed.permission.to_dict(),
            "orders": [
                item.to_dict() for item in self.orders
            ],
            "global_issues": [
                issue.to_dict()
                for issue in self.global_issues
            ],
            "summary": summary.to_dict(),
        }


@dataclass(frozen=True)
class BrokerRequestSpec:
    plan_id: str
    request_class: str
    symbol: str
    qty: Decimal
    side: str
    time_in_force: str
    client_order_id: str
    limit_price: Decimal | None = None
    extended_hours: bool = False
    local_sdk_validated: bool = False
    order_class: str = "simple"
    stop_price: Decimal | None = None
    trail_price: Decimal | None = None
    trail_percent: Decimal | None = None
    take_profit_limit_price: Decimal | None = None
    stop_loss_stop_price: Decimal | None = None
    stop_loss_limit_price: Decimal | None = None
    protection_role: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "request_class": self.request_class,
            "symbol": self.symbol,
            "qty": decimal_text(self.qty),
            "side": self.side,
            "time_in_force": self.time_in_force,
            "limit_price": (
                decimal_text(self.limit_price)
                if self.limit_price is not None
                else None
            ),
            "order_class": self.order_class,
            "stop_price": (
                decimal_text(self.stop_price)
                if self.stop_price is not None
                else None
            ),
            "trail_price": (
                decimal_text(self.trail_price)
                if self.trail_price is not None
                else None
            ),
            "trail_percent": (
                decimal_text(self.trail_percent)
                if self.trail_percent is not None
                else None
            ),
            "take_profit_limit_price": (
                decimal_text(
                    self.take_profit_limit_price
                )
                if self.take_profit_limit_price
                is not None
                else None
            ),
            "stop_loss_stop_price": (
                decimal_text(
                    self.stop_loss_stop_price
                )
                if self.stop_loss_stop_price
                is not None
                else None
            ),
            "stop_loss_limit_price": (
                decimal_text(
                    self.stop_loss_limit_price
                )
                if self.stop_loss_limit_price
                is not None
                else None
            ),
            "protection_role": self.protection_role,
            "extended_hours": self.extended_hours,
            "client_order_id": self.client_order_id,
            "local_sdk_validated": (
                self.local_sdk_validated
            ),
        }
