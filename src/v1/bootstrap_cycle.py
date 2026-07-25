import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_paths import (
    build_cycle_paths,
    build_runtime_paths,
    create_cycle_paths,
    get_active_cycle_id,
    get_project_root,
    normalize_run_date,
)
from validate_coarse_candidates import (
    DEFAULT_MAX_OUTPUT_AGE_HOURS,
    validate_coarse_candidates,
)


SCRIPT_VERSION = (
    "2026-07-23-cycle-bootstrap-v1"
)

RESUMABLE_CYCLE_STATUSES = {
    "initialized",
    "running",
    "waiting_for_review",
    "blocked_retriable",
    "failed_retriable",
}

TERMINAL_CYCLE_STATUSES = {
    "completed",
    "completed_no_action",
    "completed_with_open_orders",
    "failed_terminal",
    "cancelled",
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
    """原子保存JSON对象。"""
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


def utc_now_iso() -> str:
    """返回UTC ISO时间。"""
    return datetime.now(
        timezone.utc
    ).isoformat()


def read_cycle_status(
    cycle_state_path: Path,
) -> str | None:
    """读取轮次状态。"""
    payload = load_optional_json_object(
        cycle_state_path
    )

    if payload is None:
        return None

    status = payload.get("status")

    return (
        str(status)
        if status is not None
        else None
    )


def assess_coarse_reuse(
    *,
    coarse_workspace: Path,
    max_age_hours: float,
    force_full: bool,
) -> dict[str, Any]:
    """判断当天粗选是否可以复用。"""
    if force_full:
        return {
            "action": "run",
            "reusable": False,
            "reason": (
                "用户使用--force-full"
            ),
            "validation": None,
        }

    required_paths = (
        coarse_workspace
        / "output"
        / "coarse_candidates.json",
        coarse_workspace
        / "schemas"
        / "coarse_candidates.schema.json",
        coarse_workspace
        / "data"
        / "snapshots"
        / "coarse_universe_input.json",
    )

    missing = [
        str(path)
        for path in required_paths
        if not path.exists()
    ]

    if missing:
        return {
            "action": "run",
            "reusable": False,
            "reason": (
                "当天粗选结果或校验输入缺失"
            ),
            "missing_paths": missing,
            "validation": None,
        }

    try:
        validation = (
            validate_coarse_candidates(
                workspace=coarse_workspace,
                max_age_hours=(
                    max_age_hours
                ),
            )
        )
    except Exception as error:
        return {
            "action": "run",
            "reusable": False,
            "reason": (
                "粗选校验器执行失败"
            ),
            "validation_error": str(error),
            "validation": None,
        }

    if validation.get("valid") is True:
        return {
            "action": "reuse",
            "reusable": True,
            "reason": (
                "当天粗选结果存在且通过当前校验"
            ),
            "validation": {
                "valid": True,
                "selection_count": (
                    validation.get(
                        "selection_count"
                    )
                ),
                "unique_selection_count": (
                    validation.get(
                        "unique_selection_count"
                    )
                ),
                "warnings": (
                    validation.get(
                        "warnings",
                        [],
                    )
                ),
            },
        }

    return {
        "action": "run",
        "reusable": False,
        "reason": (
            "当天粗选结果未通过当前校验"
        ),
        "validation": {
            "valid": False,
            "errors": validation.get(
                "errors",
                [],
            ),
            "warnings": validation.get(
                "warnings",
                [],
            ),
        },
    }


def choose_cycle(
    *,
    run_date: str,
    project_root: Path,
    force_new_cycle: bool,
) -> tuple[Any, bool, str | None]:
    """
    选择恢复未完成轮次或创建新轮次。

    返回：
    - CyclePaths
    - 是否恢复
    - 前一个active_cycle_id
    """
    previous_active = (
        get_active_cycle_id(
            run_date,
            project_root=project_root,
        )
    )

    if (
        previous_active is not None
        and not force_new_cycle
    ):
        previous_paths = (
            build_cycle_paths(
                cycle_id=previous_active,
                run_date=run_date,
                project_root=project_root,
            )
        )

        previous_status = (
            read_cycle_status(
                previous_paths.cycle_state
            )
        )

        if (
            previous_paths
            .cycle_directory
            .exists()
            and (
                previous_status
                in RESUMABLE_CYCLE_STATUSES
                or previous_status is None
            )
        ):
            return (
                previous_paths,
                True,
                previous_active,
            )

    new_paths = create_cycle_paths(
        run_date,
        project_root=project_root,
    )

    return (
        new_paths,
        False,
        previous_active,
    )


def build_initial_cycle_state(
    *,
    cycle_paths: Any,
    resumed: bool,
    previous_active_cycle_id: (
        str | None
    ),
    no_need_review: bool,
    force_full: bool,
    force_rebalance: bool,
    coarse_plan: dict[str, Any],
) -> dict[str, Any]:
    """构造初始或恢复后的轮次状态。"""
    existing = load_optional_json_object(
        cycle_paths.cycle_state
    )

    now = utc_now_iso()

    if existing is not None:
        state = existing
        state["updated_at"] = now
        state["status"] = (
            state.get("status")
            or "initialized"
        )
        state["resume_count"] = int(
            state.get(
                "resume_count",
                0,
            )
        ) + 1

        invocation_history = (
            state.setdefault(
                "invocation_history",
                [],
            )
        )

        if isinstance(
            invocation_history,
            list,
        ):
            invocation_history.append(
                {
                    "started_at": now,
                    "mode": "resume",
                    "no_need_review": (
                        no_need_review
                    ),
                    "force_full": (
                        force_full
                    ),
                    "force_rebalance": (
                        force_rebalance
                    ),
                }
            )

        return state

    review_mode = (
        "skip"
        if no_need_review
        else "prompt_after_portfolio"
    )

    return {
        "schema_version": "1.0",
        "run_date": (
            cycle_paths.run_date
        ),
        "cycle_id": (
            cycle_paths.cycle_id
        ),
        "created_at": now,
        "updated_at": now,
        "status": "initialized",
        "resume_count": 0,
        "previous_active_cycle_id": (
            previous_active_cycle_id
        ),
        "invocation": {
            "no_need_review": (
                no_need_review
            ),
            "force_full": force_full,
            "force_rebalance": (
                force_rebalance
            ),
        },
        "review": {
            "mode": review_mode,
            "status": (
                "skipped_by_flag"
                if no_need_review
                else "pending"
            ),
            "user_review_path": str(
                cycle_paths.user_review
            ),
        },
        "stage_plan": {
            "maintain_previous_orders": {
                "action": "evaluate",
                "reason": (
                    "每次主函数启动都应先维护"
                    "上一轮订单和日报"
                ),
            },
            "coarse_selection": (
                coarse_plan
            ),
            "portfolio_decision": {
                "action": (
                    "run"
                    if force_rebalance
                    else "evaluate_then_run_or_reuse"
                ),
                "reason": (
                    "强制重新分配剩余仓位"
                    if force_rebalance
                    else (
                        "根据持仓、挂单、剩余资金、"
                        "上轮未采用动作和计划年龄判断"
                    )
                ),
            },
            "execution_review": {
                "action": "run",
                "reason": (
                    "每轮必须刷新交易状态和报价，"
                    "再由第三阶段决定执行"
                ),
            },
            "broker_submission": {
                "action": "after_python_preorder_validation",
                "reason": (
                    "Codex不直接提交订单"
                ),
            },
            "report_maintenance": {
                "action": "next_invocation",
                "reason": (
                    "下一次主函数启动时维护"
                    "上一轮订单结果和日报"
                ),
            },
        },
        "stages": {
            "maintenance": {
                "status": "pending",
            },
            "coarse_selection": {
                "status": (
                    "reusable"
                    if coarse_plan.get(
                        "reusable"
                    )
                    else "pending"
                ),
            },
            "portfolio_decision": {
                "status": "pending",
            },
            "user_review": {
                "status": (
                    "skipped"
                    if no_need_review
                    else "pending"
                ),
            },
            "execution_review": {
                "status": "pending",
            },
            "broker_submission": {
                "status": "pending",
            },
        },
        "paths": {
            "cycle_directory": str(
                cycle_paths
                .cycle_directory
            ),
            "portfolio_workspace": str(
                cycle_paths
                .portfolio_workspace
            ),
            "execution_workspace": str(
                cycle_paths
                .execution_workspace
            ),
            "user_review": str(
                cycle_paths.user_review
            ),
            "execution_input": str(
                cycle_paths.execution_input
            ),
            "execution_decision": str(
                cycle_paths.execution_decision
            ),
            "broker_submission": str(
                cycle_paths.broker_submission
            ),
            "cycle_record": str(
                cycle_paths.cycle_record
            ),
        },
        "invocation_history": [
            {
                "started_at": now,
                "mode": (
                    "resume"
                    if resumed
                    else "new_cycle"
                ),
                "no_need_review": (
                    no_need_review
                ),
                "force_full": (
                    force_full
                ),
                "force_rebalance": (
                    force_rebalance
                ),
            }
        ],
    }


def update_daily_state(
    *,
    state_path: Path,
    cycle_state: dict[str, Any],
    resumed: bool,
) -> None:
    """更新日期级decision_state.json。"""
    state = (
        load_optional_json_object(
            state_path
        )
        or {
            "schema_version": "1.0",
            "run_date": (
                cycle_state["run_date"]
            ),
            "stages": {},
            "cycles": [],
        }
    )

    now = utc_now_iso()
    cycle_id = cycle_state[
        "cycle_id"
    ]

    state["run_date"] = (
        cycle_state["run_date"]
    )
    state["active_cycle_id"] = (
        cycle_id
    )
    state["latest_cycle_id"] = (
        cycle_id
    )
    state["updated_at"] = now

    runtime = state.setdefault(
        "runtime",
        {},
    )

    if isinstance(runtime, dict):
        runtime["active_cycle_id"] = (
            cycle_id
        )
        runtime["latest_cycle_id"] = (
            cycle_id
        )
        runtime["updated_at"] = now

    cycles = state.setdefault(
        "cycles",
        [],
    )

    if not isinstance(cycles, list):
        cycles = []
        state["cycles"] = cycles

    existing_record = None

    for record in cycles:
        if (
            isinstance(record, dict)
            and record.get("cycle_id")
            == cycle_id
        ):
            existing_record = record
            break

    if existing_record is None:
        cycles.append(
            {
                "cycle_id": cycle_id,
                "created_at": (
                    cycle_state.get(
                        "created_at"
                    )
                ),
                "updated_at": now,
                "status": (
                    cycle_state.get(
                        "status"
                    )
                ),
                "resumed": resumed,
                "cycle_state_path": (
                    cycle_state.get(
                        "paths",
                        {},
                    ).get(
                        "cycle_directory"
                    )
                    + "/cycle_state.json"
                    if isinstance(
                        cycle_state.get(
                            "paths"
                        ),
                        dict,
                    )
                    else None
                ),
            }
        )
    else:
        existing_record[
            "updated_at"
        ] = now
        existing_record[
            "status"
        ] = cycle_state.get(
            "status"
        )
        existing_record["resumed"] = (
            resumed
        )

    save_json_atomically(
        state_path,
        state,
    )


def initialize_user_review(
    *,
    cycle_paths: Any,
    no_need_review: bool,
) -> None:
    """
    自动模式立即写空意见。

    人工模式暂时不创建文件，
    后续主函数在第二阶段结束后询问。
    """
    if not no_need_review:
        return

    if cycle_paths.user_review.exists():
        return

    save_json_atomically(
        cycle_paths.user_review,
        {
            "schema_version": "1.0",
            "run_date": (
                cycle_paths.run_date
            ),
            "cycle_id": (
                cycle_paths.cycle_id
            ),
            "created_at": utc_now_iso(),
            "review_mode": (
                "skipped_by_flag"
            ),
            "raw_comment": "",
            "constraints": [],
            "preferences": [],
            "trade_requests": [],
        },
    )


def bootstrap_cycle(
    *,
    run_date: str,
    no_need_review: bool,
    force_full: bool,
    force_rebalance: bool,
    force_new_cycle: bool,
    coarse_max_age_hours: float,
) -> dict[str, Any]:
    """创建或恢复本轮运行上下文。"""
    project_root = get_project_root()

    runtime_paths = (
        build_runtime_paths(
            run_date,
            project_root=project_root,
        )
    )

    runtime_paths.run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    runtime_paths.cycles_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    coarse_plan = (
        assess_coarse_reuse(
            coarse_workspace=(
                runtime_paths
                .coarse_workspace
            ),
            max_age_hours=(
                coarse_max_age_hours
            ),
            force_full=force_full,
        )
    )

    (
        cycle_paths,
        resumed,
        previous_active,
    ) = choose_cycle(
        run_date=run_date,
        project_root=project_root,
        force_new_cycle=(
            force_new_cycle
        ),
    )

    cycle_paths.cycle_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    cycle_state = (
        build_initial_cycle_state(
            cycle_paths=cycle_paths,
            resumed=resumed,
            previous_active_cycle_id=(
                previous_active
            ),
            no_need_review=(
                no_need_review
            ),
            force_full=force_full,
            force_rebalance=(
                force_rebalance
            ),
            coarse_plan=coarse_plan,
        )
    )

    save_json_atomically(
        cycle_paths.cycle_state,
        cycle_state,
    )

    initialize_user_review(
        cycle_paths=cycle_paths,
        no_need_review=no_need_review,
    )

    update_daily_state(
        state_path=(
            runtime_paths.decision_state
        ),
        cycle_state=cycle_state,
        resumed=resumed,
    )

    return {
        "run_date": run_date,
        "cycle_id": (
            cycle_paths.cycle_id
        ),
        "resumed": resumed,
        "cycle_directory": str(
            cycle_paths.cycle_directory
        ),
        "cycle_state": str(
            cycle_paths.cycle_state
        ),
        "decision_state": str(
            runtime_paths.decision_state
        ),
        "coarse_action": (
            coarse_plan.get("action")
        ),
        "coarse_reusable": (
            coarse_plan.get(
                "reusable"
            )
        ),
        "coarse_reason": (
            coarse_plan.get("reason")
        ),
        "review_mode": (
            "skip"
            if no_need_review
            else "prompt_after_portfolio"
        ),
        "portfolio_action": (
            cycle_state.get(
                "stage_plan",
                {},
            ).get(
                "portfolio_decision",
                {},
            ).get("action")
        ),
    }


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    parser = argparse.ArgumentParser(
        description=(
            "创建或恢复WA Trader v1"
            "同日多轮运行上下文"
        )
    )

    parser.add_argument(
        "--run-date",
        help=(
            "纽约运行日期YYYY-MM-DD；"
            "默认使用当前纽约日期"
        ),
    )

    parser.add_argument(
        "--no-need-review",
        "--no_need_review",
        dest="no_need_review",
        action="store_true",
        help=(
            "第二阶段后跳过人工意见，"
            "自动继续"
        ),
    )

    parser.add_argument(
        "--force-full",
        action="store_true",
        help=(
            "强制重跑当天第一阶段粗选"
        ),
    )

    parser.add_argument(
        "--force-rebalance",
        action="store_true",
        help=(
            "强制重跑第二阶段并重新分配仓位"
        ),
    )

    parser.add_argument(
        "--new-cycle",
        action="store_true",
        help=(
            "即使存在未完成active cycle"
            "也创建新轮次"
        ),
    )

    parser.add_argument(
        "--coarse-max-age-hours",
        type=float,
        default=(
            DEFAULT_MAX_OUTPUT_AGE_HOURS
        ),
        help=(
            "当天粗选结果最大复用年龄，"
            "默认24小时"
        ),
    )

    arguments = parser.parse_args()

    if (
        arguments.coarse_max_age_hours
        <= 0
    ):
        parser.error(
            "--coarse-max-age-hours"
            "必须大于0"
        )

    try:
        run_date = normalize_run_date(
            arguments.run_date
        )

        result = bootstrap_cycle(
            run_date=run_date,
            no_need_review=(
                arguments.no_need_review
            ),
            force_full=(
                arguments.force_full
            ),
            force_rebalance=(
                arguments.force_rebalance
            ),
            force_new_cycle=(
                arguments.new_cycle
            ),
            coarse_max_age_hours=(
                arguments
                .coarse_max_age_hours
            ),
        )

        print("运行轮次初始化成功")
        print(
            f"纽约日期：{result['run_date']}"
        )
        print(
            f"cycle_id：{result['cycle_id']}"
        )
        print(
            "轮次模式："
            + (
                "恢复未完成轮次"
                if result["resumed"]
                else "创建新轮次"
            )
        )
        print(
            "第一阶段计划："
            f"{result['coarse_action']}"
        )
        print(
            "第一阶段原因："
            f"{result['coarse_reason']}"
        )
        print(
            "第二阶段计划："
            f"{result['portfolio_action']}"
        )
        print(
            "人工复查模式："
            f"{result['review_mode']}"
        )
        print(
            "轮次目录："
            f"{result['cycle_directory']}"
        )
        print(
            "轮次状态："
            f"{result['cycle_state']}"
        )
        print(
            "日期状态："
            f"{result['decision_state']}"
        )

        return 0

    except Exception as error:
        print("运行轮次初始化失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
