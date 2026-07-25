import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_VERSION = (
    "2026-07-23-cycle-runtime-paths-v2"
)

NEW_YORK_TIMEZONE = ZoneInfo(
    "America/New_York"
)

CANONICAL_RUNTIME_DIRECTORY = (
    "decision_runtime"
)
CANONICAL_STATE_FILENAME = (
    "decision_state.json"
)
CANONICAL_CYCLES_DIRECTORY = "cycles"
CANONICAL_CYCLE_STATE_FILENAME = (
    "cycle_state.json"
)

CYCLE_ID_PATTERN = re.compile(
    r"^\d{8}T\d{6}(?:-\d{2})?$"
)

STAGE_WORKSPACE_NAMES = {
    "coarse_selection": (
        "coarse_workspace"
    ),
    "portfolio_decision": (
        "portfolio_workspace"
    ),
    "execution_review": (
        "execution_workspace"
    ),
}


@dataclass(frozen=True)
class RuntimePaths:
    """
    WA Trader单个纽约日期的日期级路径。

    coarse_workspace仍然是日期级。
    portfolio_workspace和execution_workspace
    暂时保留为旧路径，供迁移期间兼容使用。
    新代码应通过CyclePaths访问第二、第三阶段。
    """

    project_root: Path
    run_date: str
    runtime_root: Path
    run_directory: Path
    decision_state: Path
    coarse_workspace: Path
    cycles_directory: Path
    portfolio_workspace: Path
    execution_workspace: Path
    daily_report: Path
    weekly_report: Path


@dataclass(frozen=True)
class CyclePaths:
    """
    同一纽约日期内一次组合决策和执行轮次的路径。

    日期级共享：
    - coarse_workspace
    - decision_state.json

    轮次级独立：
    - portfolio_workspace
    - execution_workspace
    - user_review.json
    - execution_input.json
    - execution_decision.json
    - broker_submission.json
    """

    project_root: Path
    run_date: str
    cycle_id: str
    runtime_root: Path
    run_directory: Path
    decision_state: Path
    coarse_workspace: Path
    cycles_directory: Path
    cycle_directory: Path
    cycle_state: Path
    portfolio_workspace: Path
    execution_workspace: Path
    user_review: Path
    execution_input: Path
    execution_decision: Path
    broker_submission: Path
    cycle_record: Path


def get_project_root() -> Path:
    """返回项目根目录。"""
    return (
        Path(__file__)
        .resolve()
        .parent
        .parent
        .parent
    )


def get_new_york_now() -> datetime:
    """返回当前纽约时间。"""
    return datetime.now(
        NEW_YORK_TIMEZONE
    )


def normalize_run_date(
    value: (
        str
        | date
        | datetime
        | None
    ) = None,
) -> str:
    """把运行日期统一为YYYY-MM-DD。"""
    if value is None:
        return (
            get_new_york_now()
            .date()
            .isoformat()
        )

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=NEW_YORK_TIMEZONE
            )
        else:
            value = value.astimezone(
                NEW_YORK_TIMEZONE
            )

        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if not isinstance(value, str):
        raise TypeError(
            "run_date必须是字符串、date、"
            "datetime或None"
        )

    try:
        parsed = date.fromisoformat(
            value
        )
    except ValueError as error:
        raise ValueError(
            "run_date必须采用YYYY-MM-DD"
            f"格式：{value}"
        ) from error

    return parsed.isoformat()


def normalize_cycle_id(
    value: str,
) -> str:
    """
    校验并返回轮次ID。

    格式：
    - 20260723T093100
    - 20260723T093100-01
    """
    if not isinstance(value, str):
        raise TypeError(
            "cycle_id必须是字符串"
        )

    normalized = value.strip()

    if not CYCLE_ID_PATTERN.fullmatch(
        normalized
    ):
        raise ValueError(
            "cycle_id必须采用"
            "YYYYMMDDTHHMMSS或"
            "YYYYMMDDTHHMMSS-NN格式："
            f"{value}"
        )

    return normalized


def generate_cycle_id(
    value: datetime | None = None,
) -> str:
    """根据纽约时间生成基础轮次ID。"""
    current = (
        value
        if value is not None
        else get_new_york_now()
    )

    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TIMEZONE
        )
    else:
        current = current.astimezone(
            NEW_YORK_TIMEZONE
        )

    return current.strftime(
        "%Y%m%dT%H%M%S"
    )


def get_cycle_run_date(
    cycle_id: str,
) -> str:
    """从cycle_id提取纽约运行日期。"""
    normalized = normalize_cycle_id(
        cycle_id
    )

    parsed = datetime.strptime(
        normalized[:8],
        "%Y%m%d",
    )

    return parsed.date().isoformat()


def get_iso_week_name(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
) -> str:
    """返回YYYY-Www格式的ISO周名称。"""
    normalized = normalize_run_date(
        run_date
    )
    parsed = date.fromisoformat(
        normalized
    )
    iso_year, iso_week, _ = (
        parsed.isocalendar()
    )

    return (
        f"{iso_year}-W"
        f"{iso_week:02d}"
    )


def build_runtime_paths(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> RuntimePaths:
    """
    构造纽约日期级路径。

    注意：
    portfolio_workspace和execution_workspace
    是迁移兼容路径。新轮次代码应使用
    build_cycle_paths()。
    """
    root = (
        project_root.resolve()
        if project_root is not None
        else get_project_root()
    )

    normalized_date = normalize_run_date(
        run_date
    )

    runtime_root = (
        root
        / CANONICAL_RUNTIME_DIRECTORY
    )

    run_directory = (
        runtime_root
        / normalized_date
    )

    weekly_name = get_iso_week_name(
        normalized_date
    )

    return RuntimePaths(
        project_root=root,
        run_date=normalized_date,
        runtime_root=runtime_root,
        run_directory=run_directory,
        decision_state=(
            run_directory
            / CANONICAL_STATE_FILENAME
        ),
        coarse_workspace=(
            run_directory
            / STAGE_WORKSPACE_NAMES[
                "coarse_selection"
            ]
        ),
        cycles_directory=(
            run_directory
            / CANONICAL_CYCLES_DIRECTORY
        ),
        # 以下两个路径仅用于迁移兼容。
        portfolio_workspace=(
            run_directory
            / STAGE_WORKSPACE_NAMES[
                "portfolio_decision"
            ]
        ),
        execution_workspace=(
            run_directory
            / STAGE_WORKSPACE_NAMES[
                "execution_review"
            ]
        ),
        daily_report=(
            root
            / "reports"
            / "daily"
            / f"{normalized_date}.md"
        ),
        weekly_report=(
            root
            / "reports"
            / "weekly"
            / f"{weekly_name}.md"
        ),
    )


def build_cycle_paths(
    *,
    cycle_id: str,
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    project_root: Path | None = None,
) -> CyclePaths:
    """构造指定轮次的全部规范路径。"""
    normalized_cycle_id = (
        normalize_cycle_id(cycle_id)
    )

    cycle_run_date = (
        get_cycle_run_date(
            normalized_cycle_id
        )
    )

    normalized_run_date = (
        normalize_run_date(
            run_date
        )
        if run_date is not None
        else cycle_run_date
    )

    if (
        normalized_run_date
        != cycle_run_date
    ):
        raise ValueError(
            "cycle_id日期与run_date"
            "不一致："
            f"cycle_id={normalized_cycle_id}；"
            f"run_date={normalized_run_date}"
        )

    runtime = build_runtime_paths(
        normalized_run_date,
        project_root=project_root,
    )

    cycle_directory = (
        runtime.cycles_directory
        / normalized_cycle_id
    )

    return CyclePaths(
        project_root=runtime.project_root,
        run_date=runtime.run_date,
        cycle_id=normalized_cycle_id,
        runtime_root=runtime.runtime_root,
        run_directory=(
            runtime.run_directory
        ),
        decision_state=(
            runtime.decision_state
        ),
        coarse_workspace=(
            runtime.coarse_workspace
        ),
        cycles_directory=(
            runtime.cycles_directory
        ),
        cycle_directory=(
            cycle_directory
        ),
        cycle_state=(
            cycle_directory
            / CANONICAL_CYCLE_STATE_FILENAME
        ),
        portfolio_workspace=(
            cycle_directory
            / STAGE_WORKSPACE_NAMES[
                "portfolio_decision"
            ]
        ),
        execution_workspace=(
            cycle_directory
            / STAGE_WORKSPACE_NAMES[
                "execution_review"
            ]
        ),
        user_review=(
            cycle_directory
            / "user_review.json"
        ),
        execution_input=(
            cycle_directory
            / "execution_input.json"
        ),
        execution_decision=(
            cycle_directory
            / "execution_decision.json"
        ),
        broker_submission=(
            cycle_directory
            / "broker_submission.json"
        ),
        cycle_record=(
            cycle_directory
            / "cycle_record.json"
        ),
    )


def ensure_run_directory(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> RuntimePaths:
    """创建日期级运行目录。"""
    paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    paths.run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    paths.cycles_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return paths


def ensure_cycle_directory(
    *,
    cycle_id: str,
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    project_root: Path | None = None,
) -> CyclePaths:
    """创建轮次目录并返回规范路径。"""
    paths = build_cycle_paths(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
    )

    paths.cycle_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return paths


def allocate_cycle_id(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """
    为一次新运行分配唯一cycle_id。

    同一秒重复创建时追加-01、-02等。
    """
    normalized_run_date = (
        normalize_run_date(
            run_date
        )
    )

    current = (
        now
        if now is not None
        else get_new_york_now()
    )

    if current.tzinfo is None:
        current = current.replace(
            tzinfo=NEW_YORK_TIMEZONE
        )
    else:
        current = current.astimezone(
            NEW_YORK_TIMEZONE
        )

    if (
        current.date().isoformat()
        != normalized_run_date
    ):
        current = datetime.combine(
            date.fromisoformat(
                normalized_run_date
            ),
            current.timetz(),
        )

    base_id = generate_cycle_id(
        current
    )

    runtime = ensure_run_directory(
        normalized_run_date,
        project_root=project_root,
    )

    candidate = base_id

    for suffix in range(0, 100):
        if suffix > 0:
            candidate = (
                f"{base_id}-{suffix:02d}"
            )

        candidate_path = (
            runtime.cycles_directory
            / candidate
        )

        if not candidate_path.exists():
            return candidate

    raise RuntimeError(
        "同一秒内轮次数量超过99，"
        "无法分配cycle_id"
    )


def create_cycle_paths(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
    now: datetime | None = None,
) -> CyclePaths:
    """分配并创建一个新轮次目录。"""
    cycle_id = allocate_cycle_id(
        run_date,
        project_root=project_root,
        now=now,
    )

    return ensure_cycle_directory(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
    )


def list_cycle_ids(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> list[str]:
    """列出指定日期的全部合法轮次ID。"""
    runtime = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    if not runtime.cycles_directory.exists():
        return []

    cycle_ids = [
        path.name
        for path in (
            runtime.cycles_directory
            .iterdir()
        )
        if (
            path.is_dir()
            and CYCLE_ID_PATTERN.fullmatch(
                path.name
            )
        )
    ]

    return sorted(cycle_ids)


def find_latest_cycle_id(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> str:
    """返回指定日期最近的轮次ID。"""
    cycle_ids = list_cycle_ids(
        run_date,
        project_root=project_root,
    )

    if not cycle_ids:
        raise FileNotFoundError(
            "该运行日期没有任何cycle："
            f"{normalize_run_date(run_date)}"
        )

    return cycle_ids[-1]


def find_latest_cycle_paths(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> CyclePaths:
    """返回指定日期最近的轮次路径。"""
    cycle_id = find_latest_cycle_id(
        run_date,
        project_root=project_root,
    )

    return build_cycle_paths(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
    )


def load_decision_state(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """读取日期级decision_state.json。"""
    paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    if not paths.decision_state.exists():
        return {}

    with paths.decision_state.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            "decision_state.json"
            "顶层必须是对象"
        )

    return payload


def get_active_cycle_id(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
) -> str | None:
    """读取日期级状态中的active_cycle_id。"""
    state = load_decision_state(
        run_date,
        project_root=project_root,
    )

    value = state.get(
        "active_cycle_id"
    )

    if value is None:
        runtime = state.get(
            "runtime",
            {},
        )

        if isinstance(runtime, dict):
            value = runtime.get(
                "active_cycle_id"
            )

    if value is None:
        return None

    try:
        return normalize_cycle_id(
            str(value)
        )
    except (TypeError, ValueError):
        return None


def resolve_cycle_id(
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    cycle_id: str | None = None,
    project_root: Path | None = None,
    allow_latest: bool = True,
) -> str:
    """
    按显式参数、active_cycle_id、最新轮次
    的顺序解析cycle_id。
    """
    if cycle_id is not None:
        normalized = normalize_cycle_id(
            cycle_id
        )

        expected_date = normalize_run_date(
            run_date
        )

        if (
            get_cycle_run_date(normalized)
            != expected_date
        ):
            raise ValueError(
                "显式cycle_id与run_date"
                "不一致"
            )

        return normalized

    active = get_active_cycle_id(
        run_date,
        project_root=project_root,
    )

    if active is not None:
        active_path = build_cycle_paths(
            cycle_id=active,
            run_date=run_date,
            project_root=project_root,
        )

        if active_path.cycle_directory.exists():
            return active

    if allow_latest:
        return find_latest_cycle_id(
            run_date,
            project_root=project_root,
        )

    raise FileNotFoundError(
        "没有可解析的active cycle"
    )


def get_stage_workspace(
    stage: str,
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    *,
    project_root: Path | None = None,
    cycle_id: str | None = None,
) -> Path:
    """
    返回指定阶段工作区。

    - coarse_selection始终是日期级；
    - portfolio_decision和execution_review
      在提供cycle_id时返回轮次级路径；
    - 未提供cycle_id时返回旧兼容路径。
    """
    runtime = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    if stage == "coarse_selection":
        return runtime.coarse_workspace

    if stage not in {
        "portfolio_decision",
        "execution_review",
    }:
        raise ValueError(
            "未知阶段："
            f"{stage}；允许值为"
            + ", ".join(
                sorted(
                    STAGE_WORKSPACE_NAMES
                )
            )
        )

    if cycle_id is None:
        return (
            runtime.portfolio_workspace
            if stage
            == "portfolio_decision"
            else runtime.execution_workspace
        )

    cycle = build_cycle_paths(
        cycle_id=cycle_id,
        run_date=run_date,
        project_root=project_root,
    )

    return (
        cycle.portfolio_workspace
        if stage == "portfolio_decision"
        else cycle.execution_workspace
    )


def find_latest_stage_workspace(
    stage: str,
    *,
    project_root: Path | None = None,
    run_date: (
        str
        | date
        | datetime
        | None
    ) = None,
    cycle_id: str | None = None,
    include_legacy: bool = True,
) -> Path:
    """
    寻找最近存在的指定阶段工作区。

    新结构优先级：
    1. 显式cycle_id；
    2. 当前日期active/latest cycle；
    3. 其他日期最新cycle；
    4. 旧日期级兼容工作区。
    """
    root = (
        project_root.resolve()
        if project_root is not None
        else get_project_root()
    )

    if stage == "coarse_selection":
        today_workspace = (
            get_stage_workspace(
                stage,
                run_date,
                project_root=root,
            )
        )

        if today_workspace.exists():
            return today_workspace

        runtime_root = (
            root
            / CANONICAL_RUNTIME_DIRECTORY
        )

        candidates = sorted(
            runtime_root.glob(
                "*/coarse_workspace"
            ),
            key=lambda path: (
                path.parent.name
            ),
            reverse=True,
        )

        if candidates:
            return candidates[0]

        raise FileNotFoundError(
            "没有找到阶段工作区："
            f"{stage}"
        )

    if stage not in {
        "portfolio_decision",
        "execution_review",
    }:
        raise ValueError(
            f"未知阶段：{stage}"
        )

    normalized_run_date = (
        normalize_run_date(run_date)
    )

    if cycle_id is not None:
        explicit = get_stage_workspace(
            stage,
            normalized_run_date,
            project_root=root,
            cycle_id=cycle_id,
        )

        if explicit.exists():
            return explicit

        raise FileNotFoundError(
            "显式轮次工作区不存在："
            f"{explicit}"
        )

    try:
        resolved_cycle_id = (
            resolve_cycle_id(
                normalized_run_date,
                project_root=root,
            )
        )

        resolved = get_stage_workspace(
            stage,
            normalized_run_date,
            project_root=root,
            cycle_id=resolved_cycle_id,
        )

        if resolved.exists():
            return resolved

    except FileNotFoundError:
        pass

    runtime_root = (
        root
        / CANONICAL_RUNTIME_DIRECTORY
    )

    workspace_name = (
        STAGE_WORKSPACE_NAMES[stage]
    )

    cycle_candidates = sorted(
        runtime_root.glob(
            "*/cycles/*/"
            f"{workspace_name}"
        ),
        key=lambda path: (
            path.parents[2].name,
            path.parent.name,
        ),
        reverse=True,
    )

    if cycle_candidates:
        return cycle_candidates[0]

    if include_legacy:
        legacy_candidates = sorted(
            runtime_root.glob(
                f"*/{workspace_name}"
            ),
            key=lambda path: (
                path.parent.name
            ),
            reverse=True,
        )

        if legacy_candidates:
            return legacy_candidates[0]

    raise FileNotFoundError(
        "没有找到阶段工作区："
        f"{stage}"
    )


def load_daily_decision_policy(
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """读取每日决策策略。"""
    root = (
        project_root.resolve()
        if project_root is not None
        else get_project_root()
    )

    policy_path = (
        root
        / "config"
        / "daily_decision_policy.json"
    )

    if not policy_path.exists():
        return {}

    with policy_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            "daily_decision_policy.json"
            "顶层必须是对象"
        )

    return payload


def check_policy_path_consistency(
    *,
    project_root: Path | None = None,
) -> list[str]:
    """
    检查配置中的路径模板是否与规范路径一致。

    只返回警告，不自动修改配置。
    """
    policy = load_daily_decision_policy(
        project_root=project_root
    )

    warnings: list[str] = []

    state_config = policy.get(
        "decision_state",
        {},
    )

    if isinstance(state_config, dict):
        configured = state_config.get(
            "path_template"
        )

        canonical = (
            "decision_runtime/{date}/"
            "decision_state.json"
        )

        if (
            isinstance(configured, str)
            and configured != canonical
        ):
            warnings.append(
                "decision_state.path_template"
                "与规范路径不一致："
                f"配置={configured}；"
                f"规范={canonical}"
            )

    daily_config = (
        policy.get("reports", {})
        .get("daily", {})
        if isinstance(
            policy.get("reports", {}),
            dict,
        )
        else {}
    )

    if isinstance(daily_config, dict):
        configured = daily_config.get(
            "path_template"
        )

        canonical = (
            "reports/daily/{date}.md"
        )

        if (
            isinstance(configured, str)
            and configured != canonical
        ):
            warnings.append(
                "日报path_template与规范路径"
                "不一致："
                f"配置={configured}；"
                f"规范={canonical}"
            )

    weekly_config = (
        policy.get("reports", {})
        .get("weekly", {})
        if isinstance(
            policy.get("reports", {}),
            dict,
        )
        else {}
    )

    if isinstance(weekly_config, dict):
        configured = weekly_config.get(
            "path_template"
        )

        canonical = (
            "reports/weekly/"
            "{iso_year}-W{iso_week}.md"
        )

        if (
            isinstance(configured, str)
            and configured != canonical
        ):
            warnings.append(
                "周报path_template与规范路径"
                "不一致："
                f"配置={configured}；"
                f"规范={canonical}"
            )

    return warnings


def main() -> int:
    """显示当前日期和轮次路径示例。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    paths = build_runtime_paths()

    print(
        f"纽约运行日期：{paths.run_date}"
    )
    print(
        f"运行根目录：{paths.runtime_root}"
    )
    print(
        f"日期目录：{paths.run_directory}"
    )
    print(
        f"状态文件：{paths.decision_state}"
    )
    print(
        f"粗选工作区：{paths.coarse_workspace}"
    )
    print(
        f"轮次根目录：{paths.cycles_directory}"
    )
    print(
        "旧组合工作区（兼容）："
        f"{paths.portfolio_workspace}"
    )
    print(
        "旧执行工作区（兼容）："
        f"{paths.execution_workspace}"
    )
    print(
        f"日报：{paths.daily_report}"
    )
    print(
        f"周报：{paths.weekly_report}"
    )

    active_cycle_id = (
        get_active_cycle_id()
    )

    print()
    print(
        "当前active_cycle_id："
        f"{active_cycle_id or '无'}"
    )

    try:
        latest_cycle = (
            find_latest_cycle_paths()
        )

        print(
            "最近轮次："
            f"{latest_cycle.cycle_id}"
        )
        print(
            "最近轮次目录："
            f"{latest_cycle.cycle_directory}"
        )
        print(
            "轮次组合工作区："
            f"{latest_cycle.portfolio_workspace}"
        )
        print(
            "轮次执行工作区："
            f"{latest_cycle.execution_workspace}"
        )

    except FileNotFoundError:
        print(
            "最近轮次：无"
        )

    warnings = (
        check_policy_path_consistency()
    )

    if warnings:
        print()
        print("配置路径警告：")

        for warning in warnings:
            print(f"- {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())