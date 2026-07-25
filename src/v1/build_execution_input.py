import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_paths import (
    find_latest_stage_workspace,
    get_project_root,
)
from validate_portfolio_decision import (
    validate_portfolio_decision,
)


SCRIPT_VERSION = (
    "2026-07-22-portfolio-validity-24h-v2"
)

DEFAULT_MAX_ACCOUNT_AGE_MINUTES = 20
DEFAULT_MAX_PORTFOLIO_AGE_MINUTES = 1440

REQUIRED_LIVE_SNAPSHOTS = (
    "account.json",
    "positions.json",
    "open_orders.json",
)

OPTIONAL_LIVE_SNAPSHOTS = (
    "today_orders.json",
    "assets.json",
)


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


def load_optional_json_object(
    path: Path,
) -> dict[str, Any] | None:
    """读取可选JSON对象。"""
    if not path.exists():
        return None

    return load_json_object(path)


def save_json_atomically(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """原子保存JSON。"""
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    os.replace(
        temporary_path,
        path,
    )


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

    return parsed.astimezone(
        timezone.utc
    )


def find_generated_at(
    payload: dict[str, Any],
) -> datetime | None:
    """在常见位置读取快照生成时间。"""
    candidates = (
        payload.get("generated_at"),
        payload.get("updated_at"),
        (
            payload.get("data", {})
            .get("generated_at")
            if isinstance(
                payload.get("data"),
                dict,
            )
            else None
        ),
    )

    for candidate in candidates:
        parsed = parse_datetime(candidate)

        if parsed is not None:
            return parsed

    return None


def file_modified_at(
    path: Path,
) -> datetime:
    """读取文件修改时间。"""
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        timezone.utc,
    )


def snapshot_freshness(
    path: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """计算快照时间和年龄。"""
    generated_at = (
        find_generated_at(payload)
        or file_modified_at(path)
    )

    now = datetime.now(timezone.utc)
    age_minutes = max(
        0.0,
        (
            now - generated_at
        ).total_seconds()
        / 60.0,
    )

    return {
        "path": str(path),
        "generated_at": (
            generated_at.isoformat()
        ),
        "age_minutes": round(
            age_minutes,
            3,
        ),
    }


def extract_records(
    payload: dict[str, Any],
    candidate_paths: tuple[
        tuple[str, ...],
        ...,
    ],
) -> list[dict[str, Any]]:
    """从不同快照结构中提取记录数组。"""
    for candidate_path in (
        candidate_paths
    ):
        current: Any = payload

        for key in candidate_path:
            if not isinstance(
                current,
                dict,
            ):
                current = None
                break

            current = current.get(key)

        if isinstance(current, list):
            return [
                item
                for item in current
                if isinstance(item, dict)
            ]

    return []


def get_position_quantity(
    record: dict[str, Any],
) -> float:
    """读取带方向持仓数量。"""
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
    positions_snapshot: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造最新持仓索引。"""
    records = extract_records(
        positions_snapshot,
        (
            ("data", "positions"),
            ("positions",),
            ("data",),
        ),
    )

    lookup: dict[str, dict[str, Any]] = {}

    for record in records:
        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

        lookup[symbol] = {
            "quantity": (
                get_position_quantity(record)
            ),
            "raw": record,
        }

    return lookup


def build_open_order_lookup(
    orders_snapshot: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """按标的整理最新未完成订单。"""
    records = extract_records(
        orders_snapshot,
        (
            ("data", "orders"),
            ("orders",),
            ("data",),
        ),
    )

    lookup: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for index, record in enumerate(
        records
    ):
        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

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

        lookup.setdefault(
            symbol,
            [],
        ).append(
            {
                "canonical_identity": (
                    identities[0]
                    if identities
                    else f"{symbol}:{index}"
                ),
                "identities": identities,
                "raw": record,
            }
        )

    return lookup


def build_candidate_lookup(
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

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        symbol = normalize_symbol(
            candidate.get("symbol")
        )

        if symbol:
            lookup[symbol] = candidate

    if len(lookup) != 60:
        raise ValueError(
            "执行复核输入要求60只候选，"
            f"实际={len(lookup)}"
        )

    return lookup


def build_decision_lookup(
    portfolio_output: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """构造第二阶段逐标的决策索引。"""
    decisions = portfolio_output.get(
        "position_decisions",
        [],
    )

    if not isinstance(decisions, list):
        raise ValueError(
            "position_decisions必须是数组"
        )

    lookup: dict[str, dict[str, Any]] = {}

    for decision in decisions:
        if not isinstance(decision, dict):
            continue

        symbol = normalize_symbol(
            decision.get("symbol")
        )

        if not symbol:
            continue

        if symbol in lookup:
            raise ValueError(
                f"第二阶段决策重复：{symbol}"
            )

        lookup[symbol] = decision

    return lookup


def extract_bars(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """提取K线数组。"""
    data = snapshot.get("data", {})

    if isinstance(data, dict):
        bars = data.get("bars", [])

        if isinstance(bars, list):
            return [
                bar
                for bar in bars
                if isinstance(bar, dict)
            ]

    bars = snapshot.get("bars", [])

    if isinstance(bars, list):
        return [
            bar
            for bar in bars
            if isinstance(bar, dict)
        ]

    return []


def build_market_record(
    *,
    project_root: Path,
    run_date: str,
    symbol: str,
) -> dict[str, Any]:
    """读取执行复核时最新可用本地行情。"""
    daily_path = (
        project_root
        / "data"
        / "bars"
        / "daily"
        / f"{symbol}.json"
    )

    intraday_path = (
        project_root
        / "data"
        / "bars"
        / "intraday"
        / run_date
        / f"{symbol}.json"
    )

    daily_snapshot = (
        load_optional_json_object(
            daily_path
        )
    )
    intraday_snapshot = (
        load_optional_json_object(
            intraday_path
        )
    )

    daily_bars = (
        extract_bars(daily_snapshot)
        if daily_snapshot is not None
        else []
    )
    intraday_bars = (
        extract_bars(intraday_snapshot)
        if intraday_snapshot is not None
        else []
    )

    intraday_data = (
        intraday_snapshot.get(
            "data",
            {},
        )
        if isinstance(
            intraday_snapshot,
            dict,
        )
        else {}
    )

    if not isinstance(
        intraday_data,
        dict,
    ):
        intraday_data = {}

    return {
        "daily_source_path": (
            str(daily_path)
            if daily_path.exists()
            else None
        ),
        "intraday_source_path": (
            str(intraday_path)
            if intraday_path.exists()
            else None
        ),
        "latest_daily_bar": (
            daily_bars[-1]
            if daily_bars
            else None
        ),
        "latest_intraday_bar": (
            intraday_bars[-1]
            if intraday_bars
            else None
        ),
        "intraday_status": (
            intraday_snapshot.get(
                "status"
            )
            if isinstance(
                intraday_snapshot,
                dict,
            )
            else "missing"
        ),
        "intraday_window_status": (
            intraday_data.get(
                "window_status"
            )
        ),
        "intraday_generated_at": (
            intraday_snapshot.get(
                "generated_at"
            )
            if isinstance(
                intraday_snapshot,
                dict,
            )
            else None
        ),
        "intraday_bar_count": len(
            intraday_bars
        ),
    }


def infer_market_phase(
    market_records: list[dict[str, Any]],
    portfolio_output: dict[str, Any],
) -> str:
    """根据最新盘中快照推导市场阶段。"""
    statuses = {
        record.get(
            "intraday_window_status"
        )
        for record in market_records
        if record.get(
            "intraday_window_status"
        )
    }

    mapping = {
        "before_delayed_data_available": (
            "before_market_open"
        ),
        "regular_session": (
            "regular_session"
        ),
        "after_market_close": (
            "after_market_close"
        ),
        "market_closed_weekend": (
            "market_closed_weekend"
        ),
        "market_closed_holiday": (
            "market_closed_holiday"
        ),
    }

    if len(statuses) == 1:
        only = next(iter(statuses))

        return mapping.get(
            only,
            str(only),
        )

    run_mode = portfolio_output.get(
        "run_mode"
    )

    if isinstance(run_mode, str):
        return run_mode

    return "unknown"


def get_portfolio_age_minutes(
    portfolio_output: dict[str, Any],
) -> float:
    """计算第二阶段结果年龄。"""
    generated_at = parse_datetime(
        portfolio_output.get(
            "generated_at"
        )
    )

    if generated_at is None:
        raise ValueError(
            "portfolio_decision.generated_at"
            "不是有效时间"
        )

    return max(
        0.0,
        (
            datetime.now(timezone.utc)
            - generated_at
        ).total_seconds()
        / 60.0,
    )


def build_execution_input(
    *,
    max_account_age_minutes: float,
    max_portfolio_age_minutes: float,
) -> Path:
    """生成第三阶段窄化执行复核输入。"""
    project_root = get_project_root()

    portfolio_workspace = (
        find_latest_stage_workspace(
            "portfolio_decision",
            project_root=project_root,
        ).resolve()
    )

    validation = (
        validate_portfolio_decision(
            workspace=portfolio_workspace,
        )
    )

    if not validation["valid"]:
        raise RuntimeError(
            "第二阶段组合计划未通过校验：\n- "
            + "\n- ".join(
                validation["errors"]
            )
        )

    portfolio_output_path = (
        portfolio_workspace
        / "output"
        / "portfolio_decision.json"
    )
    portfolio_input_path = (
        portfolio_workspace
        / "data"
        / "snapshots"
        / "portfolio_input.json"
    )

    portfolio_output = load_json_object(
        portfolio_output_path
    )
    portfolio_input = load_json_object(
        portfolio_input_path
    )

    portfolio_age_minutes = (
        get_portfolio_age_minutes(
            portfolio_output
        )
    )

    if (
        portfolio_age_minutes
        > max_portfolio_age_minutes
    ):
        raise RuntimeError(
            "第二阶段组合计划过旧："
            f"{portfolio_age_minutes:.1f}分钟；"
            f"上限={max_portfolio_age_minutes:.1f}分钟。"
            "组合计划允许隔夜复用，但超过24小时后"
            "必须重新运行第二阶段。"
        )

    run_date = portfolio_output.get(
        "run_date"
    )

    if not isinstance(run_date, str):
        raise ValueError(
            "组合计划缺少run_date"
        )

    snapshot_directory = (
        project_root
        / "data"
        / "snapshots"
    )

    live_snapshots: dict[
        str,
        dict[str, Any],
    ] = {}
    freshness: dict[
        str,
        dict[str, Any],
    ] = {}
    stale_required: list[str] = []

    for filename in (
        REQUIRED_LIVE_SNAPSHOTS
    ):
        path = (
            snapshot_directory
            / filename
        )
        payload = load_json_object(path)
        key = filename.removesuffix(
            ".json"
        )

        live_snapshots[key] = payload
        freshness[key] = (
            snapshot_freshness(
                path,
                payload,
            )
        )

        if (
            freshness[key]["age_minutes"]
            > max_account_age_minutes
        ):
            stale_required.append(
                (
                    f"{filename}="
                    f"{freshness[key]['age_minutes']:.1f}分钟"
                )
            )

    for filename in (
        OPTIONAL_LIVE_SNAPSHOTS
    ):
        path = (
            snapshot_directory
            / filename
        )
        payload = (
            load_optional_json_object(path)
        )

        if payload is None:
            continue

        key = filename.removesuffix(
            ".json"
        )
        live_snapshots[key] = payload
        freshness[key] = (
            snapshot_freshness(
                path,
                payload,
            )
        )

    if stale_required:
        raise RuntimeError(
            "执行复核要求最新账户快照，"
            "以下文件过旧："
            + ", ".join(stale_required)
            + "。请先重新运行账户、持仓和订单抓取。"
        )

    candidate_lookup = (
        build_candidate_lookup(
            portfolio_input
        )
    )
    decision_lookup = (
        build_decision_lookup(
            portfolio_output
        )
    )
    position_lookup = (
        build_position_lookup(
            live_snapshots["positions"]
        )
    )
    order_lookup = (
        build_open_order_lookup(
            live_snapshots["open_orders"]
        )
    )

    review_symbols = sorted(
        set(decision_lookup)
        | set(position_lookup)
        | set(order_lookup)
    )

    if not review_symbols:
        raise RuntimeError(
            "没有需要进入执行复核的标的"
        )

    action_records: list[
        dict[str, Any]
    ] = []
    market_records: list[
        dict[str, Any]
    ] = []

    portfolio_network = (
        portfolio_output.get(
            "network_research",
            {},
        )
    )

    if not isinstance(
        portfolio_network,
        dict,
    ):
        portfolio_network = {}

    for symbol in review_symbols:
        decision = decision_lookup.get(
            symbol
        )
        candidate = candidate_lookup.get(
            symbol
        )
        position = position_lookup.get(
            symbol,
            {
                "quantity": 0.0,
                "raw": None,
            },
        )
        open_orders = order_lookup.get(
            symbol,
            [],
        )

        market = build_market_record(
            project_root=project_root,
            run_date=run_date,
            symbol=symbol,
        )
        market_records.append(market)

        quantity = float(
            position.get(
                "quantity",
                0.0,
            )
        )

        decision_name = (
            decision.get("decision")
            if isinstance(decision, dict)
            else "manual_review"
        )

        proposed_new = (
            decision.get(
                "proposed_new_position"
            )
            is True
            if isinstance(decision, dict)
            else False
        )

        screen_eligible = (
            decision.get(
                "screen_new_position_eligible"
            )
            is True
            if isinstance(decision, dict)
            else False
        )

        action_records.append(
            {
                "symbol": symbol,
                "portfolio_decision": (
                    decision
                ),
                "candidate_context": (
                    {
                        "coarse_selection": (
                            candidate.get(
                                "coarse_selection"
                            )
                        ),
                        "python_screen": (
                            candidate.get(
                                "python_screen"
                            )
                        ),
                        "daily_summary": (
                            candidate.get(
                                "daily",
                                {},
                            ).get("summary")
                            if isinstance(
                                candidate,
                                dict,
                            )
                            and isinstance(
                                candidate.get(
                                    "daily"
                                ),
                                dict,
                            )
                            else None
                        ),
                    }
                    if isinstance(
                        candidate,
                        dict,
                    )
                    else None
                ),
                "latest_position": (
                    position
                ),
                "latest_open_orders": (
                    open_orders
                ),
                "latest_market_data": (
                    market
                ),
                "preliminary_gate_observations": {
                    "has_portfolio_decision": (
                        decision is not None
                    ),
                    "is_in_validated_60": (
                        candidate is not None
                    ),
                    "portfolio_network_success": (
                        portfolio_network.get(
                            "status"
                        )
                        == "success"
                    ),
                    "screen_new_position_eligible": (
                        screen_eligible
                    ),
                    "actual_quantity_is_zero": (
                        abs(quantity) < 1e-9
                    ),
                    "portfolio_proposed_new_position": (
                        proposed_new
                    ),
                    "portfolio_decision_type": (
                        decision_name
                    ),
                    "requires_fresh_intraday_confirmation": (
                        decision.get(
                            "requires_fresh_intraday_confirmation"
                        )
                        is True
                        if isinstance(
                            decision,
                            dict,
                        )
                        else True
                    ),
                },
            }
        )

    market_phase = infer_market_phase(
        market_records,
        portfolio_output,
    )

    zero_position_opening_phase_gate = (
        market_phase
        == "regular_session"
    )

    missing_decisions = [
        record["symbol"]
        for record in action_records
        if record[
            "portfolio_decision"
        ]
        is None
    ]

    output_payload = {
        "schema_version": "1.0",
        "stage": (
            "execution_review_input"
        ),
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "run_date": run_date,
        "source_contract": {
            "portfolio_workspace": str(
                portfolio_workspace
            ),
            "portfolio_output": str(
                portfolio_output_path
            ),
            "portfolio_validation": {
                "valid": True,
                "network_status": (
                    validation.get(
                        "network_status"
                    )
                ),
                "position_decision_count": (
                    validation.get(
                        "position_decision_count"
                    )
                ),
                "positive_target_count": (
                    validation.get(
                        "positive_target_count"
                    )
                ),
            },
            "portfolio_age_minutes": round(
                portfolio_age_minutes,
                3,
            ),
            "max_portfolio_age_minutes": (
                max_portfolio_age_minutes
            ),
            "portfolio_validity_model": (
                "same-run-date strategic plan may be reused "
                "for up to 24 hours; live account, positions, "
                "orders, market phase, and price data must be "
                "refreshed immediately before execution"
            ),
        },
        "live_context": {
            "snapshot_freshness": (
                freshness
            ),
            "max_required_snapshot_age_minutes": (
                max_account_age_minutes
            ),
            "account": (
                live_snapshots.get(
                    "account"
                )
            ),
            "positions": (
                live_snapshots.get(
                    "positions"
                )
            ),
            "open_orders": (
                live_snapshots.get(
                    "open_orders"
                )
            ),
            "today_orders": (
                live_snapshots.get(
                    "today_orders"
                )
            ),
            "assets": (
                live_snapshots.get(
                    "assets"
                )
            ),
        },
        "execution_gate_policy": {
            "market_phase": market_phase,
            "zero_position_opening_phase_gate": (
                zero_position_opening_phase_gate
            ),
            "outside_regular_session_zero_position_opening_blocked": True,
            "portfolio_network_status": (
                portfolio_network.get(
                    "status"
                )
            ),
            "portfolio_new_positions_permitted_by_research_status": (
                portfolio_network.get(
                    "new_positions_permitted_by_research_status"
                )
            ),
            "execution_review_may_recommend_actions": True,
            "execution_review_may_grant_final_permission": False,
            "python_final_pre_order_check_required": True,
            "python_must_refresh_before_submission": [
                "account",
                "positions",
                "open_orders",
                "latest tradable price or quote",
                "market phase",
                "asset tradability",
            ],
        },
        "review_scope": {
            "symbol_count": len(
                review_symbols
            ),
            "symbols": review_symbols,
            "portfolio_decision_symbol_count": len(
                decision_lookup
            ),
            "latest_position_symbol_count": len(
                position_lookup
            ),
            "latest_open_order_symbol_count": len(
                order_lookup
            ),
            "symbols_missing_portfolio_decision": (
                missing_decisions
            ),
        },
        "actions": action_records,
        "warnings": (
            [
                (
                    "最新持仓或挂单中存在第二阶段"
                    "没有覆盖的标的，第三阶段必须"
                    "转为manual_review，不得自动执行："
                    + ", ".join(
                        missing_decisions
                    )
                )
            ]
            if missing_decisions
            else []
        ),
    }

    output_path = (
        project_root
        / "data"
        / "snapshots"
        / "execution_input.json"
    )

    save_json_atomically(
        output_path,
        output_payload,
    )

    return output_path


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    parser = argparse.ArgumentParser(
        description=(
            "生成第三阶段窄化执行复核输入"
        )
    )

    parser.add_argument(
        "--max-account-age-minutes",
        type=float,
        default=(
            DEFAULT_MAX_ACCOUNT_AGE_MINUTES
        ),
    )

    parser.add_argument(
        "--max-portfolio-age-minutes",
        type=float,
        default=(
            DEFAULT_MAX_PORTFOLIO_AGE_MINUTES
        ),
    )

    arguments = parser.parse_args()

    if (
        arguments.max_account_age_minutes
        <= 0
    ):
        parser.error(
            "--max-account-age-minutes"
            "必须大于0"
        )

    if (
        arguments.max_portfolio_age_minutes
        <= 0
    ):
        parser.error(
            "--max-portfolio-age-minutes"
            "必须大于0"
        )

    try:
        output_path = (
            build_execution_input(
                max_account_age_minutes=(
                    arguments
                    .max_account_age_minutes
                ),
                max_portfolio_age_minutes=(
                    arguments
                    .max_portfolio_age_minutes
                ),
            )
        )

        payload = load_json_object(
            output_path
        )

        scope = payload.get(
            "review_scope",
            {},
        )
        gate = payload.get(
            "execution_gate_policy",
            {},
        )

        print(
            "第三阶段执行复核输入生成成功"
        )
        print(f"输出：{output_path}")
        print(
            "复核标的数量："
            f"{scope.get('symbol_count')}"
        )
        print(
            "第二阶段决策数量："
            f"{scope.get('portfolio_decision_symbol_count')}"
        )
        print(
            "最新持仓标的数量："
            f"{scope.get('latest_position_symbol_count')}"
        )
        print(
            "最新挂单标的数量："
            f"{scope.get('latest_open_order_symbol_count')}"
        )
        print(
            "当前市场阶段："
            f"{gate.get('market_phase')}"
        )
        print(
            "当前时段零持仓开仓门："
            f"{gate.get('zero_position_opening_phase_gate')}"
        )

        missing = scope.get(
            "symbols_missing_portfolio_decision",
            [],
        )

        if missing:
            print(
                "需要人工复核的新增标的："
                + ", ".join(missing)
            )

        return 0

    except Exception as error:
        print(
            "第三阶段执行复核输入生成失败"
        )
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())