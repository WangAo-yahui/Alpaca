import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from config import get_project_root
from fetch_account import save_json_atomically


EPSILON = 1e-6


def load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象。"""
    if not path.exists():
        raise FileNotFoundError(f"没有找到文件：{path}")

    with path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    if not isinstance(content, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")

    return content


def parse_time(value: str) -> datetime:
    """解析带时区的 ISO 8601 时间。"""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"时间缺少时区：{value}")
    return result


def add_issue(
    issues: list[dict[str, str]],
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append({"code": code, "path": path, "message": message})


def validate_schema(
    plan: dict[str, Any],
    schema: dict[str, Any],
) -> list[dict[str, str]]:
    """使用 JSON Schema 验证结构和基本类型。"""
    errors: list[dict[str, str]] = []
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    for error in sorted(
        validator.iter_errors(plan),
        key=lambda item: list(item.absolute_path),
    ):
        path = "$"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        add_issue(errors, "schema_error", path, error.message)

    return errors


def allowed_symbols(candidate_input: dict[str, Any]) -> set[str]:
    """获取本轮允许交易和持有的候选标的。"""
    return {
        str(item.get("symbol", "")).strip().upper()
        for item in candidate_input.get("selected_for_codex", [])
        if isinstance(item, dict) and str(item.get("symbol", "")).strip()
    }


def validate_portfolio(
    plan: dict[str, Any],
    policy: dict[str, Any],
    candidates: set[str],
    errors: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """验证目标组合仓位。"""
    target = plan["portfolio_target"]
    positions = target["positions"]
    cash_weight = float(target["target_cash_weight"])
    limits = policy["portfolio_limits"]

    min_cash = float(limits["min_cash_weight"])
    max_single = float(limits["max_single_position_weight"])
    max_total = float(limits["max_total_position_weight"])
    max_count = int(limits["max_position_count"])

    if cash_weight < min_cash - EPSILON:
        add_issue(
            errors,
            "cash_below_minimum",
            "$.portfolio_target.target_cash_weight",
            f"现金比例低于最低要求 {min_cash:.2%}。",
        )

    seen: set[str] = set()
    target_by_symbol: dict[str, dict[str, Any]] = {}
    active_count = 0
    position_weight = 0.0

    for index, position in enumerate(positions):
        path = f"$.portfolio_target.positions[{index}]"
        symbol = position["symbol"]
        qty = int(position["target_qty"])
        weight = float(position["target_weight"])
        decision = position["decision"]

        if symbol in seen:
            add_issue(errors, "duplicate_symbol", path, f"重复标的：{symbol}")
        seen.add(symbol)
        target_by_symbol[symbol] = position

        if symbol not in candidates:
            add_issue(
                errors,
                "symbol_outside_candidates",
                f"{path}.symbol",
                f"{symbol} 不在 selected_for_codex 中。",
            )

        if weight > max_single + EPSILON:
            add_issue(
                errors,
                "single_weight_exceeded",
                f"{path}.target_weight",
                f"{symbol} 超过单票仓位上限 {max_single:.2%}。",
            )

        if decision == "close":
            if qty != 0 or weight > EPSILON:
                add_issue(
                    errors,
                    "close_target_not_zero",
                    path,
                    "close 的目标数量和目标仓位必须为 0。",
                )
        elif qty <= 0 or weight <= 0:
            add_issue(
                errors,
                "active_target_not_positive",
                path,
                f"decision={decision} 时数量和仓位必须大于 0。",
            )

        if qty > 0 and weight > EPSILON:
            active_count += 1
            position_weight += weight

    total_weight = position_weight + cash_weight

    if abs(total_weight - 1.0) > EPSILON:
        add_issue(
            errors,
            "weights_not_one",
            "$.portfolio_target",
            f"持仓与现金之和为 {total_weight:.8f}，必须等于 1。",
        )

    if position_weight > max_total + EPSILON:
        add_issue(
            errors,
            "total_weight_exceeded",
            "$.portfolio_target.positions",
            f"总持仓比例超过上限 {max_total:.2%}。",
        )

    if active_count > max_count:
        add_issue(
            errors,
            "position_count_exceeded",
            "$.portfolio_target.positions",
            f"目标持仓数量超过上限 {max_count}。",
        )

    return target_by_symbol


def validate_stop(
    stop_loss: dict[str, Any],
    reference_price: float,
    policy: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    """验证多头止损。"""
    stop_price = float(stop_loss["stop_price"])
    limit_price = stop_loss["limit_price"]
    stop_type = stop_loss["type"]
    rules = policy["protection_policy"]["stop_loss"]

    if stop_price >= reference_price:
        add_issue(
            errors,
            "stop_not_below_reference",
            f"{path}.stop_price",
            "多头止损价必须低于参考价格。",
        )
    else:
        distance = (reference_price - stop_price) / reference_price
        minimum = float(rules["min_distance_from_entry_pct"])
        maximum = float(rules["max_distance_from_entry_pct"])

        if distance < minimum - EPSILON:
            add_issue(errors, "stop_too_close", path, f"止损距离低于 {minimum:.2%}。")
        if distance > maximum + EPSILON:
            add_issue(errors, "stop_too_far", path, f"止损距离超过 {maximum:.2%}。")

    if stop_type == "stop" and limit_price is not None:
        add_issue(errors, "stop_has_limit", path, "stop 类型不得填写 limit_price。")

    if stop_type == "stop_limit":
        if limit_price is None:
            add_issue(errors, "stop_limit_missing_limit", path, "stop_limit 缺少 limit_price。")
        elif float(limit_price) > stop_price:
            add_issue(
                errors,
                "stop_limit_relation_invalid",
                path,
                "多头卖出 stop-limit 的 limit_price 不得高于 stop_price。",
            )


def validate_take_profit(
    take_profit: float,
    reference_price: float,
    policy: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    """验证多头止盈。"""
    rules = policy["protection_policy"]["take_profit"]

    if take_profit <= reference_price:
        add_issue(errors, "take_profit_not_above_reference", path, "止盈价必须高于参考价格。")
        return

    distance = (take_profit - reference_price) / reference_price
    minimum = float(rules["min_distance_from_entry_pct"])
    maximum = float(rules["max_distance_from_entry_pct"])

    if distance < minimum - EPSILON:
        add_issue(errors, "take_profit_too_close", path, f"止盈距离低于 {minimum:.2%}。")
    if distance > maximum + EPSILON:
        add_issue(errors, "take_profit_too_far", path, f"止盈距离超过 {maximum:.2%}。")


def validate_protection(
    protection: dict[str, Any],
    primary_order: dict[str, Any] | None,
    policy: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    """验证保护模式之间的互斥关系和价格关系。"""
    mode = protection["mode"]
    protected_qty = protection["protected_qty"]
    take_profit = protection["take_profit_price"]
    stop_loss = protection["stop_loss"]
    trail_percent = protection["trail_percent"]
    tif = protection["time_in_force"]
    tranches = protection["tranches"]

    if mode == "none":
        if any(value is not None for value in (protected_qty, take_profit, stop_loss, trail_percent, tif)) or tranches:
            add_issue(errors, "none_has_protection_values", path, "mode=none 时其他保护字段必须为空。")
        return

    if protected_qty is None:
        add_issue(errors, "protected_qty_missing", path, "启用保护时必须填写 protected_qty。")
        return

    reference_price: float | None = None
    if primary_order is not None:
        reference_price = float(
            primary_order["limit_price"]
            or primary_order["stop_price"]
            or primary_order["reference_price"]
        )

    if mode in {"fixed_bracket", "tiered_fixed_brackets"}:
        if primary_order is None:
            add_issue(errors, "bracket_without_entry", path, f"{mode} 必须配合主订单。")
        elif int(protected_qty) != int(primary_order["qty"]):
            add_issue(errors, "protected_qty_mismatch", path, "protected_qty 必须等于主订单 qty。")

    if mode in {"fixed_bracket", "existing_position_oco"}:
        if take_profit is None or stop_loss is None or tif is None:
            add_issue(errors, "fixed_protection_incomplete", path, "固定保护必须包含止盈、止损和 time_in_force。")
        if trail_percent is not None or tranches:
            add_issue(errors, "fixed_protection_conflict", path, "固定保护不得同时包含动态止损或分段退出。")

        if reference_price is not None and take_profit is not None:
            validate_take_profit(float(take_profit), reference_price, policy, f"{path}.take_profit_price", errors)
        if reference_price is not None and stop_loss is not None:
            validate_stop(stop_loss, reference_price, policy, f"{path}.stop_loss", errors)

    elif mode == "standalone_trailing_stop":
        if trail_percent is None or tif is None:
            add_issue(errors, "trailing_incomplete", path, "动态止损必须包含 trail_percent 和 time_in_force。")
        if take_profit is not None or stop_loss is not None or tranches:
            add_issue(errors, "trailing_conflict", path, "动态止损不得包含固定止盈、固定止损或分段退出。")

    elif mode == "tiered_fixed_brackets":
        if not tranches:
            add_issue(errors, "tranches_missing", path, "分段保护至少需要一个 tranche。")
            return

        qty_sum = sum(int(item["qty"]) for item in tranches)
        if qty_sum != int(protected_qty):
            add_issue(
                errors,
                "tranche_qty_sum_invalid",
                f"{path}.tranches",
                f"分段数量之和 {qty_sum} 不等于 protected_qty {protected_qty}。",
            )

        prices = [float(item["take_profit_price"]) for item in tranches]
        if prices != sorted(prices):
            add_issue(errors, "tranche_prices_not_ascending", f"{path}.tranches", "分段止盈价格必须从低到高排列。")

        if reference_price is not None:
            for index, tranche in enumerate(tranches):
                tranche_path = f"{path}.tranches[{index}]"
                validate_take_profit(
                    float(tranche["take_profit_price"]),
                    reference_price,
                    policy,
                    f"{tranche_path}.take_profit_price",
                    errors,
                )
                validate_stop(
                    tranche["stop_loss"],
                    reference_price,
                    policy,
                    f"{tranche_path}.stop_loss",
                    errors,
                )


def validate_trade_plans(
    plan: dict[str, Any],
    policy: dict[str, Any],
    candidates: set[str],
    targets: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    """验证主订单和保护计划。"""
    seen_ids: set[str] = set()
    seen_symbols: set[str] = set()
    executable_count = 0
    now = datetime.now(timezone.utc)

    generated_at = parse_time(plan["generated_at"])
    age_minutes = (now - generated_at.astimezone(timezone.utc)).total_seconds() / 60
    max_age = int(policy["execution"]["plan_valid_minutes"])

    if age_minutes > max_age:
        add_issue(errors, "plan_expired", "$.generated_at", f"计划已生成 {age_minutes:.2f} 分钟，超过 {max_age} 分钟有效期。")

    for index, trade in enumerate(plan["trade_plans"]):
        path = f"$.trade_plans[{index}]"
        plan_id = trade["trade_plan_id"]
        symbol = trade["symbol"]
        intent = trade["intent"]
        primary = trade["primary_order"]

        if plan_id in seen_ids:
            add_issue(errors, "duplicate_trade_plan_id", path, f"重复 trade_plan_id：{plan_id}")
        if symbol in seen_symbols:
            add_issue(errors, "duplicate_trade_symbol", path, f"同一轮中 {symbol} 只能有一个 trade_plan。")
        seen_ids.add(plan_id)
        seen_symbols.add(symbol)

        if symbol not in candidates:
            add_issue(errors, "trade_symbol_outside_candidates", f"{path}.symbol", f"{symbol} 不在候选池中。")

        target = targets.get(symbol)
        if target is None:
            add_issue(errors, "trade_without_target", path, f"{symbol} 没有对应目标持仓。")
        elif int(target["target_qty"]) != int(trade["target_qty"]):
            add_issue(errors, "target_qty_mismatch", f"{path}.target_qty", "trade_plan 与 portfolio_target 的 target_qty 不一致。")

        if intent in {"no_trade", "protect", "replace_protection"}:
            if primary is not None:
                add_issue(errors, "unexpected_primary_order", f"{path}.primary_order", f"intent={intent} 时 primary_order 必须为 null。")
        elif primary is None:
            add_issue(errors, "primary_order_missing", f"{path}.primary_order", f"intent={intent} 时必须提供主订单。")

        if primary is not None:
            executable_count += 1
            side = primary["side"]
            order_type = primary["order_type"]
            style = primary["entry_style"]
            limit_price = primary["limit_price"]
            stop_price = primary["stop_price"]
            reference_price = float(primary["reference_price"])

            if intent in {"open", "increase"} and side != "buy":
                add_issue(errors, "entry_side_invalid", f"{path}.primary_order.side", "open/increase 必须使用 buy。")
            if intent in {"reduce", "close"} and side != "sell":
                add_issue(errors, "exit_side_invalid", f"{path}.primary_order.side", "reduce/close 必须使用 sell。")

            if order_type == "limit" and (limit_price is None or stop_price is not None):
                add_issue(errors, "limit_fields_invalid", f"{path}.primary_order", "limit 订单只填写 limit_price。")
            if order_type == "stop" and (stop_price is None or limit_price is not None):
                add_issue(errors, "stop_fields_invalid", f"{path}.primary_order", "stop 订单只填写 stop_price。")
            if order_type == "stop_limit" and (stop_price is None or limit_price is None):
                add_issue(errors, "stop_limit_fields_invalid", f"{path}.primary_order", "stop_limit 必须同时填写 stop_price 和 limit_price。")

            if style in {"immediate", "passive_pullback"} and order_type != "limit":
                add_issue(errors, "entry_style_invalid", f"{path}.primary_order.entry_style", f"{style} 必须使用 limit。")
            if style == "breakout_confirmation" and order_type not in {"stop", "stop_limit"}:
                add_issue(errors, "breakout_type_invalid", f"{path}.primary_order.entry_style", "breakout_confirmation 必须使用 stop 或 stop_limit。")

            order_price = limit_price or stop_price
            if order_price is not None:
                distance_bps = abs(float(order_price) / reference_price - 1) * 10000
                max_bps = float(policy["entry_policy"]["max_limit_distance_from_reference_bps"])
                if distance_bps > max_bps + EPSILON:
                    add_issue(errors, "order_price_too_far", f"{path}.primary_order", f"订单价距参考价 {distance_bps:.2f} bps，超过上限 {max_bps:.2f}。")

            if parse_time(primary["valid_until"]) <= now:
                add_issue(errors, "order_expired", f"{path}.primary_order.valid_until", "主订单已经过期。")

            quote_age = (now - parse_time(primary["reference_quote_at"]).astimezone(timezone.utc)).total_seconds()
            max_quote_age = int(policy["execution"]["quote_max_age_seconds"])
            if quote_age > max_quote_age:
                add_issue(
                    warnings,
                    "quote_refresh_required",
                    f"{path}.primary_order.reference_quote_at",
                    "参考报价已过期；提交前必须重新获取最新报价。",
                )

        validate_protection(trade["protection"], primary, policy, f"{path}.protection", errors)

    allowed_modes = set(policy["execution"]["allowed_run_modes"])
    if executable_count and plan["run_mode"] not in allowed_modes:
        add_issue(errors, "run_mode_not_allowed", "$.run_mode", f"run_mode={plan['run_mode']} 不允许生成可执行订单。")


def validate_order_review(
    plan: dict[str, Any],
    candidate_input: dict[str, Any],
    policy: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    """确保所有旧挂单被审阅，且不自动修改手工订单。"""
    open_ids = {
        str(order.get("client_order_id", ""))
        for order in candidate_input.get("open_orders", {}).get("orders", [])
        if isinstance(order, dict) and str(order.get("client_order_id", ""))
    }
    reviewed: set[str] = set()
    prefix = policy["order_reconciliation"]["strategy_client_order_id_prefix"]
    trade_plan_ids = {item["trade_plan_id"] for item in plan["trade_plans"]}

    for index, review in enumerate(plan["order_review"]):
        path = f"$.order_review[{index}]"
        order_id = review["client_order_id"]
        action = review["suggested_action"]
        replacement = review["replacement_trade_plan_id"]

        if order_id in reviewed:
            add_issue(errors, "duplicate_order_review", path, f"订单 {order_id} 被重复审阅。")
        reviewed.add(order_id)

        if order_id not in open_ids:
            add_issue(errors, "unknown_open_order", path, f"订单 {order_id} 不在当前未完成订单中。")

        if action in {"cancel", "replace"} and not order_id.startswith(prefix):
            add_issue(errors, "manual_order_change_forbidden", path, "非策略订单不能自动取消或替换。")

        if action == "replace" and replacement not in trade_plan_ids:
            add_issue(errors, "replacement_missing", f"{path}.replacement_trade_plan_id", "replace 必须引用有效 trade_plan_id。")
        if action != "replace" and replacement is not None:
            add_issue(errors, "unexpected_replacement", f"{path}.replacement_trade_plan_id", "只有 replace 才能填写 replacement_trade_plan_id。")

    for order_id in sorted(open_ids - reviewed):
        add_issue(errors, "open_order_not_reviewed", "$.order_review", f"未完成订单 {order_id} 尚未审阅。")


def validate_business_rules(
    plan: dict[str, Any],
    candidate_input: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """执行 JSON Schema 无法表达的业务规则。"""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    account = candidate_input.get("account", {})

    for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        if account.get(field) is True:
            add_issue(errors, "account_blocked", f"$.account.{field}", f"账户状态 {field}=true。")

    candidates = allowed_symbols(candidate_input)
    if not candidates:
        add_issue(errors, "empty_candidate_pool", "$.selected_for_codex", "候选池为空。")

    targets = validate_portfolio(plan, policy, candidates, errors)
    validate_trade_plans(plan, policy, candidates, targets, errors, warnings)
    validate_order_review(plan, candidate_input, policy, errors)

    return errors, warnings


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def validate_order_plan(plan_path: Path) -> tuple[Path, dict[str, Any]]:
    """验证订单计划并保存报告。"""
    root = get_project_root()
    plan = load_json(plan_path)
    schema = load_json(root / "schemas" / "order_plan.schema.json")
    policy = load_json(root / "config" / "order_policy.json")
    candidate_input = load_json(root / "data" / "snapshots" / "candidate_input.json")

    errors = validate_schema(plan, schema)
    warnings: list[dict[str, str]] = []

    if not errors:
        business_errors, warnings = validate_business_rules(plan, candidate_input, policy)
        errors.extend(business_errors)

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "valid" if not errors else "rejected",
        "plan_path": str(plan_path),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "execution_allowed": False,
        "note": (
            "这是计划级校验。真正下单前仍必须刷新最新报价、账户、持仓和未完成订单。"
        ),
    }

    output = root / "output" / "validation" / "order_plan_validation.json"
    save_json_atomically(output, report)
    return output, report


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Codex 生成的订单计划。")
    parser.add_argument(
        "plan_path",
        nargs="?",
        default="decision_workspace/output/order_plan.json",
        help="订单计划 JSON 路径。",
    )
    args = parser.parse_args()

    try:
        root = get_project_root()
        output, report = validate_order_plan(resolve_path(root, args.plan_path))

        print("订单计划校验完成")
        print(f"校验结果：{report['status']}")
        print(f"错误数量：{report['error_count']}")
        print(f"警告数量：{report['warning_count']}")
        print(f"报告位置：{output}")

        for issue in report["errors"]:
            print(f"错误 [{issue['code']}] {issue['path']}：{issue['message']}")
        for issue in report["warnings"]:
            print(f"警告 [{issue['code']}] {issue['path']}：{issue['message']}")

        return 0 if report["status"] == "valid" else 1

    except Exception as error:
        print("订单计划校验程序运行失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
