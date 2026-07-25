import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import (
    Draft202012Validator,
    FormatChecker,
)

from runtime_paths import (
    find_latest_stage_workspace,
    get_project_root,
)


SCRIPT_VERSION = (
    "2026-07-22-portfolio-business-validator-v1"
)

DEFAULT_MAX_OUTPUT_AGE_HOURS = 24
WEIGHT_SUM_TOLERANCE = 0.02
POSITION_WEIGHT_TOLERANCE = 0.05
QUANTITY_TOLERANCE = 1e-8
ZERO_TOLERANCE = 1e-9

FORBIDDEN_FIELD_NAMES = {
    "new_position_allowed",
    "execution_new_position_allowed",
}

POSITION_REQUIRED_DECISIONS = {
    "increase",
    "hold",
    "reduce",
    "close",
    "protect",
    "replace_protection",
}

ZERO_POSITION_DECISIONS = {
    "open",
    "watch",
    "avoid",
}


def load_json_object(
    path: Path,
) -> dict[str, Any]:
    """读取JSON对象。"""
    if not path.exists():
        raise FileNotFoundError(
            f"缺少文件：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON顶层必须是对象：{path}"
        )

    return payload


def normalize_symbol(
    value: Any,
) -> str:
    """标准化标的代码。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def safe_float(
    value: Any,
) -> float | None:
    """转换为有限浮点数。"""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def parse_datetime(
    value: Any,
) -> datetime | None:
    """解析ISO时间。"""
    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def format_schema_path(
    error: Any,
) -> str:
    """格式化Schema错误路径。"""
    parts = [
        str(item)
        for item in error.absolute_path
    ]

    return (
        "$"
        if not parts
        else "$." + ".".join(parts)
    )


def validate_schema(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """执行Draft 2020-12 Schema校验。"""
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    return [
        (
            f"{format_schema_path(error)}："
            f"{error.message}"
        )
        for error in errors
    ]


def find_forbidden_fields(
    value: Any,
    path: str = "$",
) -> list[str]:
    """递归查找已弃用或越权字段。"""
    errors: list[str] = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"

            if key in FORBIDDEN_FIELD_NAMES:
                errors.append(
                    f"{child_path}为禁止字段"
                )

            errors.extend(
                find_forbidden_fields(
                    child,
                    child_path,
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(
                find_forbidden_fields(
                    child,
                    f"{path}[{index}]",
                )
            )

    return errors


def extract_nested_records(
    payload: dict[str, Any],
    path: tuple[str, ...],
) -> list[dict[str, Any]]:
    """读取嵌套记录数组。"""
    current: Any = payload

    for key in path:
        if not isinstance(current, dict):
            return []

        current = current.get(key)

    if not isinstance(current, list):
        return []

    return [
        record
        for record in current
        if isinstance(record, dict)
    ]


def extract_candidate_lookup(
    portfolio_input: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造60只候选索引。"""
    candidates = portfolio_input.get(
        "candidates",
        [],
    )

    if not isinstance(candidates, list):
        raise ValueError(
            "portfolio_input.candidates必须是数组"
        )

    lookup: dict[str, dict[str, Any]] = {}

    for index, candidate in enumerate(
        candidates
    ):
        if not isinstance(candidate, dict):
            raise ValueError(
                f"candidates[{index}]必须是对象"
            )

        symbol = normalize_symbol(
            candidate.get("symbol")
        )

        if not symbol:
            raise ValueError(
                f"candidates[{index}]缺少symbol"
            )

        if symbol in lookup:
            raise ValueError(
                f"portfolio_input候选重复：{symbol}"
            )

        lookup[symbol] = candidate

    if len(lookup) != 60:
        raise ValueError(
            "portfolio_input必须包含恰好60只候选，"
            f"实际={len(lookup)}"
        )

    return lookup


def get_position_quantity(
    record: dict[str, Any],
) -> float:
    """从Alpaca持仓记录中读取带方向数量。"""
    quantity = None

    for key in (
        "qty",
        "quantity",
        "current_quantity",
    ):
        quantity = safe_float(
            record.get(key)
        )

        if quantity is not None:
            break

    if quantity is None:
        quantity = 0.0

    side = str(
        record.get("side", "")
    ).strip().lower()

    if side == "short" and quantity > 0:
        quantity = -quantity

    return quantity


def build_position_lookup(
    portfolio_input: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造当前真实持仓索引。"""
    account_context = (
        portfolio_input.get(
            "account_context",
            {},
        )
    )

    if not isinstance(account_context, dict):
        return {}

    positions_payload = account_context.get(
        "positions",
        {},
    )

    if not isinstance(
        positions_payload,
        dict,
    ):
        return {}

    records = extract_nested_records(
        positions_payload,
        ("data", "positions"),
    )

    lookup: dict[str, dict[str, Any]] = {}

    for record in records:
        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

        lookup[symbol] = {
            "record": record,
            "quantity": (
                get_position_quantity(record)
            ),
        }

    return lookup


def build_order_records(
    portfolio_input: dict[str, Any],
) -> list[dict[str, Any]]:
    """提取未完成订单并生成规范身份。"""
    account_context = (
        portfolio_input.get(
            "account_context",
            {},
        )
    )

    if not isinstance(account_context, dict):
        return []

    orders_payload = account_context.get(
        "open_orders",
        {},
    )

    if not isinstance(
        orders_payload,
        dict,
    ):
        return []

    records = extract_nested_records(
        orders_payload,
        ("data", "orders"),
    )

    normalized: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        symbol = normalize_symbol(
            record.get("symbol")
        )

        identities: list[str] = []

        for key in (
            "id",
            "order_id",
            "client_order_id",
        ):
            value = record.get(key)

            if value is None:
                continue

            identity = str(value).strip()

            if (
                identity
                and identity not in identities
            ):
                identities.append(identity)

        canonical_identity = (
            identities[0]
            if identities
            else f"{symbol or 'UNKNOWN'}:{index}"
        )

        normalized.append(
            {
                "index": index,
                "symbol": symbol,
                "identities": identities,
                "canonical_identity": (
                    canonical_identity
                ),
                "record": record,
            }
        )

    return normalized


def position_state(
    quantity: float,
) -> str:
    """根据带方向数量计算持仓状态。"""
    if abs(quantity) <= ZERO_TOLERANCE:
        return "zero"

    if quantity > 0:
        return "long"

    return "short"


def candidate_eligibility(
    candidate: dict[str, Any],
) -> tuple[bool, bool]:
    """读取粗选资格字段。"""
    coarse = candidate.get(
        "coarse_selection",
        {},
    )

    if not isinstance(coarse, dict):
        coarse = {}

    return (
        coarse.get("research_eligible")
        is True,
        coarse.get(
            "screen_new_position_eligible"
        )
        is True,
    )


def validate_output_freshness(
    output: dict[str, Any],
    *,
    max_age_hours: float,
) -> list[str]:
    """校验输出时间。"""
    errors: list[str] = []

    generated_at = parse_datetime(
        output.get("generated_at")
    )

    if generated_at is None:
        return [
            "generated_at不是有效ISO时间"
        ]

    now = datetime.now(timezone.utc)

    if generated_at > now + timedelta(
        minutes=10
    ):
        errors.append(
            "generated_at明显晚于当前时间"
        )

    age = now - generated_at.astimezone(
        timezone.utc
    )

    if age > timedelta(
        hours=max_age_hours
    ):
        errors.append(
            "组合决策结果已超过"
            f"{max_age_hours:g}小时"
        )

    return errors


def validate_run_identity(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
    workspace: Path,
) -> list[str]:
    """校验阶段和日期身份。"""
    errors: list[str] = []

    expected_date = portfolio_input.get(
        "run_date"
    )

    if output.get("run_date") != expected_date:
        errors.append(
            "输出run_date与portfolio_input"
            "不一致："
            f"输出={output.get('run_date')}，"
            f"输入={expected_date}"
        )

    if workspace.parent.name != expected_date:
        errors.append(
            "工作区日期与portfolio_input"
            "不一致："
            f"工作区={workspace.parent.name}，"
            f"输入={expected_date}"
        )

    if output.get("stage") != (
        "portfolio_decision"
    ):
        errors.append(
            "stage必须为portfolio_decision"
        )

    return errors


def validate_network_policy(
    output: dict[str, Any],
) -> list[str]:
    """校验联网状态与新仓研究许可。"""
    errors: list[str] = []

    network = output.get(
        "network_research",
        {},
    )

    if not isinstance(network, dict):
        return ["network_research必须是对象"]

    status = network.get("status")
    permitted = network.get(
        "new_positions_permitted_by_research_status"
    )

    if network.get("attempted") is not True:
        errors.append(
            "第二阶段必须尝试联网研究"
        )

    if (
        status == "success"
        and permitted is not True
    ):
        errors.append(
            "联网成功时"
            "new_positions_permitted_by_research_status"
            "必须为true"
        )

    if (
        status == "local_only"
        and permitted is not False
    ):
        errors.append(
            "local_only时"
            "new_positions_permitted_by_research_status"
            "必须为false"
        )

    return errors


def validate_data_quality(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
) -> list[str]:
    """校验模型回显的数据质量统计。"""
    errors: list[str] = []

    data_quality = output.get(
        "data_quality",
        {},
    )

    if not isinstance(data_quality, dict):
        return ["data_quality必须是对象"]

    source_status = (
        portfolio_input.get(
            "market_data_status",
            {},
        )
    )

    if not isinstance(source_status, dict):
        source_status = {}

    field_mapping = {
        "daily_data_complete_count": (
            "daily_complete_count"
        ),
        "intraday_success_count": (
            "intraday_success_count"
        ),
        "intraday_no_data_count": (
            "intraday_no_data_count"
        ),
        "intraday_failed_count": (
            "intraday_failed_count"
        ),
    }

    for output_key, source_key in (
        field_mapping.items()
    ):
        output_value = data_quality.get(
            output_key
        )
        source_value = source_status.get(
            source_key
        )

        if output_value != source_value:
            errors.append(
                f"data_quality.{output_key}"
                "与输入不一致："
                f"输出={output_value}，"
                f"输入={source_value}"
            )

    for field in (
        "account_snapshot_available",
        "positions_snapshot_available",
        "open_orders_snapshot_available",
    ):
        if data_quality.get(field) is not True:
            errors.append(
                f"data_quality.{field}"
                "必须为true"
            )

    return errors


def validate_entry_plan(
    *,
    symbol: str,
    plan: dict[str, Any],
) -> list[str]:
    """校验入场计划字段一致性。"""
    errors: list[str] = []

    enabled = plan.get("enabled")
    style = plan.get("style")
    reference = safe_float(
        plan.get("reference_price")
    )
    preferred = safe_float(
        plan.get("preferred_limit_price")
    )
    maximum = safe_float(
        plan.get("maximum_acceptable_price")
    )
    staging_steps = plan.get(
        "staging_steps"
    )
    horizon = plan.get("time_horizon")

    numeric_values = {
        "reference_price": reference,
        "preferred_limit_price": preferred,
        "maximum_acceptable_price": maximum,
    }

    for name, value in numeric_values.items():
        if value is None or value < 0:
            errors.append(
                f"{symbol} entry_plan.{name}"
                "必须是非负有限数"
            )

    if enabled is False:
        expected_zero = {
            "reference_price": reference,
            "preferred_limit_price": (
                preferred
            ),
            "maximum_acceptable_price": (
                maximum
            ),
        }

        if style != "none":
            errors.append(
                f"{symbol}入场未启用时"
                "style必须为none"
            )

        for name, value in (
            expected_zero.items()
        ):
            if (
                value is None
                or abs(value)
                > ZERO_TOLERANCE
            ):
                errors.append(
                    f"{symbol}入场未启用时"
                    f"{name}必须为0"
                )

        if staging_steps != 0:
            errors.append(
                f"{symbol}入场未启用时"
                "staging_steps必须为0"
            )

        if horizon != "not_applicable":
            errors.append(
                f"{symbol}入场未启用时"
                "time_horizon必须为"
                "not_applicable"
            )

    elif enabled is True:
        if style == "none":
            errors.append(
                f"{symbol}入场启用时"
                "style不能为none"
            )

        if (
            not isinstance(
                staging_steps,
                int,
            )
            or staging_steps < 1
        ):
            errors.append(
                f"{symbol}入场启用时"
                "staging_steps必须至少为1"
            )

        if (
            preferred is not None
            and maximum is not None
            and preferred > 0
            and maximum > 0
            and preferred > maximum
        ):
            errors.append(
                f"{symbol} preferred_limit_price"
                "不能高于maximum_acceptable_price"
            )

    return errors


def validate_risk_plan(
    *,
    symbol: str,
    plan: dict[str, Any],
) -> list[str]:
    """校验风险计划字段一致性。"""
    errors: list[str] = []

    action = plan.get("action")
    stop_style = plan.get("stop_style")
    stop_reference = safe_float(
        plan.get("stop_reference")
    )
    take_profit_style = plan.get(
        "take_profit_style"
    )
    take_profit_reference = safe_float(
        plan.get("take_profit_reference")
    )
    trailing_percent = safe_float(
        plan.get("trailing_percent")
    )
    maximum_loss = safe_float(
        plan.get(
            "maximum_portfolio_loss_weight"
        )
    )

    numeric_values = {
        "stop_reference": stop_reference,
        "take_profit_reference": (
            take_profit_reference
        ),
        "trailing_percent": trailing_percent,
        "maximum_portfolio_loss_weight": (
            maximum_loss
        ),
    }

    for name, value in numeric_values.items():
        if value is None or value < 0:
            errors.append(
                f"{symbol} risk_plan.{name}"
                "必须是非负有限数"
            )

    if action == "none":
        if stop_style != "none":
            errors.append(
                f"{symbol} risk action为none时"
                "stop_style必须为none"
            )

        if take_profit_style != "none":
            errors.append(
                f"{symbol} risk action为none时"
                "take_profit_style必须为none"
            )

        for name, value in (
            numeric_values.items()
        ):
            if (
                value is None
                or abs(value)
                > ZERO_TOLERANCE
            ):
                errors.append(
                    f"{symbol} risk action为none时"
                    f"{name}必须为0"
                )

    if (
        stop_style == "trailing_percent"
        and (
            trailing_percent is None
            or trailing_percent <= 0
        )
    ):
        errors.append(
            f"{symbol}移动止损要求"
            "trailing_percent大于0"
        )

    return errors


def validate_position_decisions(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
) -> tuple[
    list[str],
    list[str],
    dict[str, dict[str, Any]],
]:
    """校验组合中的逐标的决策。"""
    errors: list[str] = []
    warnings: list[str] = []

    candidate_lookup = (
        extract_candidate_lookup(
            portfolio_input
        )
    )
    position_lookup = build_position_lookup(
        portfolio_input
    )
    order_records = build_order_records(
        portfolio_input
    )

    network = output.get(
        "network_research",
        {},
    )
    network_status = (
        network.get("status")
        if isinstance(network, dict)
        else None
    )
    network_permission = (
        network.get(
            "new_positions_permitted_by_research_status"
        )
        if isinstance(network, dict)
        else None
    )

    data_quality = output.get(
        "data_quality",
        {},
    )
    overall_data_status = (
        data_quality.get("overall_status")
        if isinstance(data_quality, dict)
        else None
    )

    market_data_status = (
        portfolio_input.get(
            "market_data_status",
            {},
        )
    )
    intraday_success_count = (
        market_data_status.get(
            "intraday_success_count",
            0,
        )
        if isinstance(
            market_data_status,
            dict,
        )
        else 0
    )

    decisions = output.get(
        "position_decisions",
        [],
    )

    if not isinstance(decisions, list):
        return (
            ["position_decisions必须是数组"],
            warnings,
            {},
        )

    decision_lookup: dict[
        str,
        dict[str, Any],
    ] = {}

    for index, decision_record in enumerate(
        decisions
    ):
        if not isinstance(
            decision_record,
            dict,
        ):
            errors.append(
                f"position_decisions[{index}]"
                "必须是对象"
            )
            continue

        symbol = normalize_symbol(
            decision_record.get("symbol")
        )

        if not symbol:
            errors.append(
                f"position_decisions[{index}]"
                "缺少有效symbol"
            )
            continue

        if symbol in decision_lookup:
            errors.append(
                f"position_decisions重复：{symbol}"
            )
            continue

        decision_lookup[symbol] = (
            decision_record
        )

        candidate = candidate_lookup.get(
            symbol
        )

        if candidate is None:
            errors.append(
                f"{symbol}不属于已验证60只候选"
            )
            continue

        actual_position = (
            position_lookup.get(symbol)
        )
        actual_quantity = (
            actual_position["quantity"]
            if actual_position is not None
            else 0.0
        )
        actual_state = position_state(
            actual_quantity
        )

        output_quantity = safe_float(
            decision_record.get(
                "current_quantity"
            )
        )

        if output_quantity is None:
            errors.append(
                f"{symbol} current_quantity"
                "不是有限数"
            )
        elif not math.isclose(
            output_quantity,
            actual_quantity,
            abs_tol=QUANTITY_TOLERANCE,
            rel_tol=QUANTITY_TOLERANCE,
        ):
            errors.append(
                f"{symbol} current_quantity"
                "与本地持仓不一致："
                f"输出={output_quantity}，"
                f"实际={actual_quantity}"
            )

        if (
            decision_record.get(
                "current_position_state"
            )
            != actual_state
        ):
            errors.append(
                f"{symbol} current_position_state"
                "与实际不一致："
                f"输出="
                f"{decision_record.get('current_position_state')}，"
                f"实际={actual_state}"
            )

        (
            expected_research,
            expected_screen,
        ) = candidate_eligibility(candidate)

        if (
            decision_record.get(
                "research_eligible"
            )
            is not expected_research
        ):
            errors.append(
                f"{symbol} research_eligible"
                "与粗选输入不一致"
            )

        if (
            decision_record.get(
                "screen_new_position_eligible"
            )
            is not expected_screen
        ):
            errors.append(
                f"{symbol} "
                "screen_new_position_eligible"
                "与粗选输入不一致"
            )

        decision = decision_record.get(
            "decision"
        )
        proposed_new = (
            decision_record.get(
                "proposed_new_position"
            )
        )

        if decision == "open":
            if actual_state != "zero":
                errors.append(
                    f"{symbol}已有持仓，"
                    "不得使用open"
                )

            if proposed_new is not True:
                errors.append(
                    f"{symbol} decision=open时"
                    "proposed_new_position必须为true"
                )

            if not expected_screen:
                errors.append(
                    f"{symbol}未通过基础筛选，"
                    "不得建议open"
                )

            if (
                network_status != "success"
                or network_permission is not True
            ):
                errors.append(
                    f"{symbol}未完成联网研究，"
                    "不得建议open"
                )

            if overall_data_status == "insufficient":
                errors.append(
                    f"{symbol}数据不足时不得建议open"
                )

        elif proposed_new is True:
            errors.append(
                f"{symbol}只有decision=open时"
                "proposed_new_position才可为true"
            )

        if (
            decision in POSITION_REQUIRED_DECISIONS
            and actual_state == "zero"
        ):
            errors.append(
                f"{symbol}当前零持仓，"
                f"不得使用{decision}"
            )

        if (
            decision in ZERO_POSITION_DECISIONS
            and decision != "open"
            and actual_state != "zero"
        ):
            warnings.append(
                f"{symbol}当前有持仓但使用"
                f"{decision}；请确认是否应使用"
                "hold、reduce或close"
            )

        target_weight = safe_float(
            decision_record.get(
                "target_weight"
            )
        )
        maximum_weight = safe_float(
            decision_record.get(
                "maximum_weight"
            )
        )

        if (
            target_weight is None
            or target_weight < 0
            or target_weight > 1
        ):
            errors.append(
                f"{symbol} target_weight"
                "必须在0到1之间"
            )

        if (
            maximum_weight is None
            or maximum_weight < 0
            or maximum_weight > 1
        ):
            errors.append(
                f"{symbol} maximum_weight"
                "必须在0到1之间"
            )

        if (
            target_weight is not None
            and maximum_weight is not None
            and target_weight
            > maximum_weight
            + ZERO_TOLERANCE
        ):
            errors.append(
                f"{symbol} target_weight"
                "不能高于maximum_weight"
            )

        if (
            decision in {
                "close",
                "watch",
                "avoid",
            }
            and target_weight is not None
            and abs(target_weight)
            > ZERO_TOLERANCE
        ):
            errors.append(
                f"{symbol} decision={decision}时"
                "target_weight必须为0"
            )

        if (
            intraday_success_count == 0
            and decision in {"open", "increase"}
            and decision_record.get(
                "requires_fresh_intraday_confirmation"
            )
            is not True
        ):
            errors.append(
                f"{symbol}当前没有盘中数据，"
                f"{decision}必须要求最新盘中确认"
            )

        entry_plan = decision_record.get(
            "entry_plan",
            {},
        )

        if not isinstance(entry_plan, dict):
            errors.append(
                f"{symbol} entry_plan必须是对象"
            )
        else:
            errors.extend(
                validate_entry_plan(
                    symbol=symbol,
                    plan=entry_plan,
                )
            )

        risk_plan = decision_record.get(
            "risk_plan",
            {},
        )

        if not isinstance(risk_plan, dict):
            errors.append(
                f"{symbol} risk_plan必须是对象"
            )
        else:
            errors.extend(
                validate_risk_plan(
                    symbol=symbol,
                    plan=risk_plan,
                )
            )

    required_symbols = set(
        position_lookup
    ) | {
        order["symbol"]
        for order in order_records
        if order["symbol"]
    }

    missing_required = sorted(
        required_symbols
        - set(decision_lookup)
    )

    if missing_required:
        errors.append(
            "position_decisions未覆盖当前持仓"
            "或未完成订单涉及标的："
            + ", ".join(missing_required)
        )

    missing_from_candidates = sorted(
        required_symbols
        - set(candidate_lookup)
    )

    if missing_from_candidates:
        errors.append(
            "当前持仓或未完成订单标的不在"
            "已验证60只候选中："
            + ", ".join(
                missing_from_candidates
            )
        )

    return (
        errors,
        warnings,
        decision_lookup,
    )


def validate_portfolio_weights(
    output: dict[str, Any],
    decision_lookup: dict[
        str,
        dict[str, Any],
    ],
) -> tuple[list[str], list[str]]:
    """校验组合层和逐标的目标权重。"""
    errors: list[str] = []
    warnings: list[str] = []

    strategy = output.get(
        "portfolio_strategy",
        {},
    )

    if not isinstance(strategy, dict):
        return (
            ["portfolio_strategy必须是对象"],
            warnings,
        )

    cash = safe_float(
        strategy.get("target_cash_weight")
    )
    invested = safe_float(
        strategy.get(
            "target_invested_weight"
        )
    )

    for name, value in (
        ("target_cash_weight", cash),
        ("target_invested_weight", invested),
    ):
        if (
            value is None
            or value < 0
            or value > 1
        ):
            errors.append(
                f"portfolio_strategy.{name}"
                "必须在0到1之间"
            )

    if (
        cash is not None
        and invested is not None
        and abs(
            cash + invested - 1.0
        )
        > WEIGHT_SUM_TOLERANCE
    ):
        errors.append(
            "target_cash_weight与"
            "target_invested_weight合计"
            "必须接近1："
            f"当前={cash + invested:.6f}"
        )

    positive_weights: list[float] = []

    for record in decision_lookup.values():
        weight = safe_float(
            record.get("target_weight")
        )

        if (
            weight is not None
            and weight > ZERO_TOLERANCE
        ):
            positive_weights.append(weight)

    total_target_weight = sum(
        positive_weights
    )

    if (
        invested is not None
        and abs(
            total_target_weight
            - invested
        )
        > POSITION_WEIGHT_TOLERANCE
    ):
        errors.append(
            "正目标权重总和与"
            "target_invested_weight偏差过大："
            f"逐标的={total_target_weight:.6f}，"
            f"组合={invested:.6f}"
        )

    target_count = strategy.get(
        "target_position_count"
    )

    if (
        not isinstance(target_count, int)
        or target_count < 0
    ):
        errors.append(
            "target_position_count"
            "必须是非负整数"
        )
    elif target_count != len(
        positive_weights
    ):
        warnings.append(
            "target_position_count与"
            "正目标权重标的数量不一致："
            f"声明={target_count}，"
            f"统计={len(positive_weights)}"
        )

    return errors, warnings


def validate_pending_orders(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """校验所有未完成订单均被逐笔复核。"""
    errors: list[str] = []
    warnings: list[str] = []

    actual_orders = build_order_records(
        portfolio_input
    )

    reviews = output.get(
        "pending_order_reviews",
        [],
    )

    if not isinstance(reviews, list):
        return (
            ["pending_order_reviews必须是数组"],
            warnings,
        )

    unmatched = set(
        range(len(actual_orders))
    )
    used_review_identities: set[str] = set()

    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            errors.append(
                f"pending_order_reviews[{index}]"
                "必须是对象"
            )
            continue

        identity = str(
            review.get(
                "order_identity",
                "",
            )
        ).strip()
        symbol = normalize_symbol(
            review.get("symbol")
        )

        if not identity:
            errors.append(
                f"pending_order_reviews[{index}]"
                "缺少order_identity"
            )
            continue

        if identity in used_review_identities:
            errors.append(
                "pending_order_reviews存在重复"
                f"order_identity：{identity}"
            )
            continue

        used_review_identities.add(
            identity
        )

        matching_indices = [
            order_index
            for order_index in unmatched
            if (
                identity
                in actual_orders[
                    order_index
                ]["identities"]
                or identity
                == actual_orders[
                    order_index
                ]["canonical_identity"]
            )
        ]

        if not matching_indices:
            # 当订单没有可用ID且同一symbol只有一笔时，
            # 允许使用symbol作为临时身份。
            symbol_matches = [
                order_index
                for order_index in unmatched
                if actual_orders[
                    order_index
                ]["symbol"]
                == symbol
            ]

            if len(symbol_matches) == 1:
                matching_indices = (
                    symbol_matches
                )

        if len(matching_indices) != 1:
            errors.append(
                f"无法唯一匹配未完成订单："
                f"identity={identity}，"
                f"symbol={symbol}"
            )
            continue

        matched_index = matching_indices[0]
        actual_order = actual_orders[
            matched_index
        ]
        unmatched.remove(matched_index)

        if symbol != actual_order["symbol"]:
            errors.append(
                f"订单{identity}的symbol不一致："
                f"输出={symbol}，"
                f"实际={actual_order['symbol']}"
            )

        recommendation = review.get(
            "recommendation"
        )
        replacement_needed = review.get(
            "replacement_needed"
        )

        if (
            recommendation == "replace"
            and replacement_needed is not True
        ):
            errors.append(
                f"订单{identity}建议replace时"
                "replacement_needed必须为true"
            )

        if (
            recommendation != "replace"
            and replacement_needed is True
        ):
            errors.append(
                f"订单{identity}非replace时"
                "replacement_needed必须为false"
            )

    if unmatched:
        missing = [
            actual_orders[index][
                "canonical_identity"
            ]
            for index in sorted(unmatched)
        ]

        errors.append(
            "以下未完成订单没有复核："
            + ", ".join(missing)
        )

    if (
        not actual_orders
        and reviews
    ):
        errors.append(
            "当前没有未完成订单，"
            "pending_order_reviews必须为空"
        )

    return errors, warnings


def validate_watchlist(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
) -> list[str]:
    """校验观察名单只引用60只候选且不重复。"""
    errors: list[str] = []

    candidate_lookup = (
        extract_candidate_lookup(
            portfolio_input
        )
    )

    watchlist = output.get(
        "watchlist",
        [],
    )

    if not isinstance(watchlist, list):
        return ["watchlist必须是数组"]

    seen: set[str] = set()

    for index, item in enumerate(watchlist):
        if not isinstance(item, dict):
            errors.append(
                f"watchlist[{index}]必须是对象"
            )
            continue

        symbol = normalize_symbol(
            item.get("symbol")
        )

        if not symbol:
            errors.append(
                f"watchlist[{index}]缺少symbol"
            )
            continue

        if symbol not in candidate_lookup:
            errors.append(
                f"watchlist中的{symbol}"
                "不属于60只候选"
            )

        if symbol in seen:
            errors.append(
                f"watchlist重复：{symbol}"
            )

        seen.add(symbol)

    return errors


def validate_source_references(
    output: dict[str, Any],
    *,
    workspace: Path,
    project_root: Path,
) -> tuple[list[str], list[str]]:
    """校验本地来源路径和持久研究文件约束。"""
    errors: list[str] = []
    warnings: list[str] = []

    research_files = output.get(
        "research_files",
        [],
    )

    if research_files != []:
        errors.append(
            "第二阶段research_files必须为空数组"
        )

    reference_lists: list[
        tuple[str, Any]
    ] = [
        (
            "source_references",
            output.get(
                "source_references",
                [],
            ),
        )
    ]

    decisions = output.get(
        "position_decisions",
        [],
    )

    if isinstance(decisions, list):
        for index, decision in enumerate(
            decisions
        ):
            if isinstance(decision, dict):
                reference_lists.append(
                    (
                        (
                            "position_decisions"
                            f"[{index}]."
                            "source_references"
                        ),
                        decision.get(
                            "source_references",
                            [],
                        ),
                    )
                )

    seen: set[
        tuple[str, str, str]
    ] = set()

    for list_name, references in (
        reference_lists
    ):
        if not isinstance(references, list):
            errors.append(
                f"{list_name}必须是数组"
            )
            continue

        for index, reference in enumerate(
            references
        ):
            if not isinstance(
                reference,
                dict,
            ):
                errors.append(
                    f"{list_name}[{index}]"
                    "必须是对象"
                )
                continue

            source_type = reference.get(
                "source_type"
            )
            title = str(
                reference.get(
                    "title",
                    "",
                )
            )
            location = str(
                reference.get(
                    "location",
                    "",
                )
            ).strip()

            identity = (
                str(source_type),
                title,
                location,
            )

            if identity in seen:
                warnings.append(
                    f"重复来源引用：{title}；"
                    f"{location}"
                )
            else:
                seen.add(identity)

            if source_type != "local_input":
                continue

            if not location:
                errors.append(
                    f"{list_name}[{index}]"
                    "本地来源缺少location"
                )
                continue

            raw_path = Path(location)

            candidates = (
                [raw_path]
                if raw_path.is_absolute()
                else [
                    workspace / raw_path,
                    project_root / raw_path,
                ]
            )

            if not any(
                path.exists()
                for path in candidates
            ):
                errors.append(
                    f"本地来源路径不存在："
                    f"{location}"
                )

    return errors, warnings


def infer_allowed_run_modes(
    portfolio_input: dict[str, Any],
) -> set[str]:
    """根据盘中窗口推导合理的run_mode。"""
    market_status = (
        portfolio_input.get(
            "market_data_status",
            {},
        )
    )

    if not isinstance(market_status, dict):
        return set()

    statuses = set(
        status
        for status in market_status.get(
            "intraday_window_statuses",
            [],
        )
        if isinstance(status, str)
    )

    mapping = {
        "before_delayed_data_available": {
            "before_market_open",
            "delayed_data_unavailable",
        },
        "regular_session": {
            "regular_session",
        },
        "after_market_close": {
            "after_market_close",
        },
        "market_closed_weekend": {
            "market_closed_weekend",
        },
        "market_closed_holiday": {
            "market_closed_holiday",
        },
    }

    allowed: set[str] = set()

    for status in statuses:
        allowed.update(
            mapping.get(status, set())
        )

    return allowed


def validate_run_mode(
    output: dict[str, Any],
    portfolio_input: dict[str, Any],
) -> list[str]:
    """对run_mode与行情窗口做提示性检查。"""
    allowed = infer_allowed_run_modes(
        portfolio_input
    )

    if not allowed:
        return []

    run_mode = output.get("run_mode")

    if run_mode not in allowed:
        return [
            "run_mode与盘中窗口状态可能不一致："
            f"输出={run_mode}，"
            f"允许={sorted(allowed)}"
        ]

    return []


def validate_portfolio_decision(
    *,
    workspace: Path,
    max_age_hours: float = (
        DEFAULT_MAX_OUTPUT_AGE_HOURS
    ),
    output_path: Path | None = None,
) -> dict[str, Any]:
    """执行第二阶段Schema和业务校验。"""
    project_root = get_project_root()
    workspace = workspace.resolve()

    expected_workspace = (
        workspace.parent
        / "portfolio_workspace"
    ).resolve()

    if workspace != expected_workspace:
        raise ValueError(
            "第二阶段工作区不符合统一规范："
            f"{workspace}"
        )

    if output_path is None:
        output_path = (
            workspace
            / "output"
            / "portfolio_decision.json"
        )
    elif not output_path.is_absolute():
        output_path = (
            workspace / output_path
        )

    schema_path = (
        workspace
        / "schemas"
        / "portfolio_decision.schema.json"
    )
    input_path = (
        workspace
        / "data"
        / "snapshots"
        / "portfolio_input.json"
    )

    output = load_json_object(output_path)
    schema = load_json_object(schema_path)
    portfolio_input = load_json_object(
        input_path
    )

    errors: list[str] = []
    warnings: list[str] = []

    errors.extend(
        validate_schema(
            output,
            schema,
        )
    )

    errors.extend(
        find_forbidden_fields(output)
    )

    errors.extend(
        validate_output_freshness(
            output,
            max_age_hours=max_age_hours,
        )
    )

    errors.extend(
        validate_run_identity(
            output,
            portfolio_input,
            workspace,
        )
    )

    errors.extend(
        validate_network_policy(output)
    )

    errors.extend(
        validate_data_quality(
            output,
            portfolio_input,
        )
    )

    (
        decision_errors,
        decision_warnings,
        decision_lookup,
    ) = validate_position_decisions(
        output,
        portfolio_input,
    )

    errors.extend(decision_errors)
    warnings.extend(decision_warnings)

    (
        weight_errors,
        weight_warnings,
    ) = validate_portfolio_weights(
        output,
        decision_lookup,
    )

    errors.extend(weight_errors)
    warnings.extend(weight_warnings)

    (
        order_errors,
        order_warnings,
    ) = validate_pending_orders(
        output,
        portfolio_input,
    )

    errors.extend(order_errors)
    warnings.extend(order_warnings)

    errors.extend(
        validate_watchlist(
            output,
            portfolio_input,
        )
    )

    (
        source_errors,
        source_warnings,
    ) = validate_source_references(
        output,
        workspace=workspace,
        project_root=project_root,
    )

    errors.extend(source_errors)
    warnings.extend(source_warnings)

    warnings.extend(
        validate_run_mode(
            output,
            portfolio_input,
        )
    )

    strategy = output.get(
        "portfolio_strategy",
        {},
    )

    positive_target_count = sum(
        1
        for decision in (
            decision_lookup.values()
        )
        if (
            safe_float(
                decision.get(
                    "target_weight"
                )
            )
            or 0.0
        )
        > ZERO_TOLERANCE
    )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "workspace": str(workspace),
        "output_path": str(
            output_path.resolve()
        ),
        "run_date": output.get(
            "run_date"
        ),
        "network_status": (
            output.get(
                "network_research",
                {},
            ).get("status")
            if isinstance(
                output.get(
                    "network_research",
                    {},
                ),
                dict,
            )
            else None
        ),
        "position_decision_count": len(
            decision_lookup
        ),
        "positive_target_count": (
            positive_target_count
        ),
        "target_position_count": (
            strategy.get(
                "target_position_count"
            )
            if isinstance(strategy, dict)
            else None
        ),
        "pending_order_review_count": len(
            output.get(
                "pending_order_reviews",
                [],
            )
            if isinstance(
                output.get(
                    "pending_order_reviews",
                    [],
                ),
                list,
            )
            else []
        ),
    }


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    parser = argparse.ArgumentParser(
        description=(
            "校验第二阶段组合决策"
        )
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "portfolio_workspace路径；"
            "默认使用纽约当天或最新工作区"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "待校验JSON；默认使用"
            "output/portfolio_decision.json"
        ),
    )

    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=(
            DEFAULT_MAX_OUTPUT_AGE_HOURS
        ),
        help="允许的最大结果年龄，默认24小时",
    )

    arguments = parser.parse_args()

    if arguments.max_age_hours <= 0:
        parser.error(
            "--max-age-hours必须大于0"
        )

    try:
        project_root = get_project_root()

        workspace = (
            arguments.workspace
            if arguments.workspace
            else find_latest_stage_workspace(
                "portfolio_decision",
                project_root=project_root,
            )
        )

        if not workspace.is_absolute():
            workspace = (
                project_root / workspace
            )

        result = validate_portfolio_decision(
            workspace=workspace,
            max_age_hours=(
                arguments.max_age_hours
            ),
            output_path=arguments.output,
        )

        print("第二阶段组合决策校验完成")
        print(
            f"工作区：{result['workspace']}"
        )
        print(
            "逐标的决策数量："
            f"{result['position_decision_count']}"
        )
        print(
            "正目标权重数量："
            f"{result['positive_target_count']}"
        )
        print(
            "声明目标持仓数量："
            f"{result['target_position_count']}"
        )
        print(
            "挂单复核数量："
            f"{result['pending_order_review_count']}"
        )
        print(
            "联网状态："
            f"{result['network_status']}"
        )
        print(
            "校验结果："
            + (
                "通过"
                if result["valid"]
                else "失败"
            )
        )

        if result["errors"]:
            print()
            print("错误：")

            for error in result["errors"]:
                print(f"- {error}")

        if result["warnings"]:
            print()
            print("警告：")

            for warning in result["warnings"]:
                print(f"- {warning}")

        return (
            0
            if result["valid"]
            else 1
        )

    except Exception as error:
        print("第二阶段组合决策校验失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
