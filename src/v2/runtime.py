from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_VERSION = "2026-07-25-v2-runtime-stage-c5-v1"

NEW_YORK_TZ = ZoneInfo("America/New_York")

RUNTIME_ROOT_NAME = "decision_runtime_v2"
REPORT_ROOT_PARTS = ("reports", "v2")

RUN_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CYCLE_ID_PATTERN = re.compile(r"^\d{8}T\d{6}$")
IDENTITY_COMPONENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
)


@dataclass(frozen=True)
class DailyPaths:
    project_root: Path
    run_date: str
    profile_id: str
    strategy_id: str
    strategy_version: str

    runtime_root: Path
    identity_root: Path
    day_directory: Path
    daily_state: Path

    coarse_directory: Path
    coarse_current: Path
    coarse_revisions: Path
    coarse_input: Path
    coarse_output: Path
    coarse_validation: Path
    coarse_codex_call: Path
    coarse_workspace: Path

    cycles_directory: Path
    daily_report: Path


@dataclass(frozen=True)
class CyclePaths:
    project_root: Path
    run_date: str
    cycle_id: str
    profile_id: str
    strategy_id: str
    strategy_version: str

    runtime_root: Path
    identity_root: Path
    day_directory: Path
    daily_state: Path
    coarse_directory: Path
    coarse_current: Path
    coarse_revisions: Path
    coarse_input: Path
    coarse_output: Path
    coarse_validation: Path
    coarse_codex_call: Path
    coarse_workspace: Path
    cycles_directory: Path

    cycle_directory: Path
    cycle_state: Path
    base_snapshot: Path
    initial_guidance: Path
    user_review: Path
    cycle_summary: Path

    portfolio_directory: Path
    portfolio_input: Path
    portfolio_output: Path
    portfolio_validation: Path
    portfolio_workspace: Path

    execution_directory: Path
    execution_input: Path
    execution_output: Path
    execution_validation: Path
    execution_workspace: Path

    orders_directory: Path
    proposed_orders: Path
    validated_orders: Path
    broker_submission: Path
    reconciliation: Path

    daily_report: Path


@dataclass(frozen=True)
class CoarseRevisionPaths:
    input_signature: str
    revision_directory: Path
    input: Path
    output: Path
    validation: Path
    codex_call: Path
    workspace: Path


@dataclass(frozen=True)
class SharedDataPaths:
    root: Path
    universe: Path
    market: Path
    daily: Path
    intraday: Path
    quotes: Path
    assets: Path
    public_research_cache: Path


def get_project_root() -> Path:
    """
    返回项目根目录。

    文件位置预期为：
    <project>/src/v2/runtime.py
    """
    return Path(__file__).resolve().parents[2]


def get_new_york_now() -> datetime:
    """返回当前纽约时间。"""
    return datetime.now(NEW_YORK_TZ)


def normalize_run_date(
    value: str | date | datetime | None = None,
) -> str:
    """
    将日期统一为纽约日期 YYYY-MM-DD。
    """
    if value is None:
        return get_new_york_now().date().isoformat()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=NEW_YORK_TZ)
        else:
            value = value.astimezone(NEW_YORK_TZ)

        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if not isinstance(value, str):
        raise TypeError(
            "run_date必须是str、date、datetime或None"
        )

    normalized = value.strip()

    if not RUN_DATE_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"run_date格式错误：{value}；必须为YYYY-MM-DD"
        )

    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(
            f"run_date不是有效日期：{value}"
        ) from error

    return parsed.isoformat()


def normalize_cycle_id(value: str) -> str:
    """
    校验轮次ID。

    格式：
    YYYYMMDDTHHMMSS
    """
    if not isinstance(value, str):
        raise TypeError("cycle_id必须是字符串")

    normalized = value.strip()

    if not CYCLE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "cycle_id格式错误："
            f"{value}；必须为YYYYMMDDTHHMMSS"
        )

    try:
        datetime.strptime(normalized, "%Y%m%dT%H%M%S")
    except ValueError as error:
        raise ValueError(
            f"cycle_id不是有效时间：{value}"
        ) from error

    return normalized


def cycle_id_to_run_date(cycle_id: str) -> str:
    """从cycle_id提取纽约日期。"""
    normalized = normalize_cycle_id(cycle_id)
    parsed = datetime.strptime(
        normalized[:8],
        "%Y%m%d",
    )
    return parsed.date().isoformat()


def generate_cycle_id(
    value: datetime | None = None,
) -> str:
    """
    根据纽约时间生成基础轮次ID。
    """
    current = value or get_new_york_now()

    if current.tzinfo is None:
        current = current.replace(tzinfo=NEW_YORK_TZ)
    else:
        current = current.astimezone(NEW_YORK_TZ)

    return current.strftime("%Y%m%dT%H%M%S")


def normalize_identity_component(
    value: str,
    *,
    field: str,
) -> str:
    normalized = str(value).strip()
    if not IDENTITY_COMPONENT_PATTERN.fullmatch(
        normalized
    ):
        raise ValueError(
            f"{field}格式无效：{value}"
        )
    return normalized


def build_shared_data_paths(
    *,
    project_root: Path | None = None,
) -> SharedDataPaths:
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else get_project_root()
    )
    shared = root / "shared_data"
    market = shared / "market"
    return SharedDataPaths(
        root=shared,
        universe=shared / "universe",
        market=market,
        daily=market / "daily",
        intraday=market / "intraday",
        quotes=market / "quotes",
        assets=shared / "assets",
        public_research_cache=(
            shared / "public_research_cache"
        ),
    )


def build_daily_paths(
    run_date: str | date | datetime | None = None,
    *,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
) -> DailyPaths:
    """
    构造日期级规范路径。
    """
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else get_project_root()
    )

    normalized_date = normalize_run_date(run_date)
    normalized_profile = normalize_identity_component(
        profile_id,
        field="profile_id",
    )
    normalized_strategy = normalize_identity_component(
        strategy_id,
        field="strategy_id",
    )
    normalized_version = normalize_identity_component(
        strategy_version,
        field="strategy_version",
    )

    runtime_root = root / RUNTIME_ROOT_NAME
    identity_root = (
        runtime_root
        / "accounts"
        / normalized_profile
        / "strategies"
        / normalized_strategy
        / normalized_version
    )
    day_directory = identity_root / normalized_date
    coarse_directory = day_directory / "coarse"
    coarse_revisions = coarse_directory / "revisions"
    cycles_directory = day_directory / "cycles"

    report_root = (
        root.joinpath(*REPORT_ROOT_PARTS)
        / "accounts"
        / normalized_profile
        / "strategies"
        / normalized_strategy
        / normalized_version
        / "daily"
    )

    return DailyPaths(
        project_root=root,
        run_date=normalized_date,
        profile_id=normalized_profile,
        strategy_id=normalized_strategy,
        strategy_version=normalized_version,
        runtime_root=runtime_root,
        identity_root=identity_root,
        day_directory=day_directory,
        daily_state=day_directory / "daily_state.json",
        coarse_directory=coarse_directory,
        coarse_current=coarse_directory / "current.json",
        coarse_revisions=coarse_revisions,
        # Compatibility aliases for Stage A-C callers.  C.5 orchestration uses
        # CoarseRevisionPaths exclusively.
        coarse_input=coarse_directory / "input.json",
        coarse_output=coarse_directory / "current.json",
        coarse_validation=(
            coarse_directory / "validation.json"
        ),
        coarse_codex_call=(
            coarse_directory / "codex_call.json"
        ),
        coarse_workspace=coarse_directory / "workspace",
        cycles_directory=cycles_directory,
        daily_report=report_root / f"{normalized_date}.md",
    )


def build_cycle_paths(
    *,
    cycle_id: str,
    run_date: str | date | datetime | None = None,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
) -> CyclePaths:
    """
    构造轮次级规范路径。
    """
    normalized_cycle_id = normalize_cycle_id(cycle_id)
    cycle_run_date = cycle_id_to_run_date(normalized_cycle_id)

    normalized_run_date = (
        normalize_run_date(run_date)
        if run_date is not None
        else cycle_run_date
    )

    if normalized_run_date != cycle_run_date:
        raise ValueError(
            "cycle_id日期与run_date不一致："
            f"cycle_id={normalized_cycle_id}；"
            f"run_date={normalized_run_date}"
        )

    daily = build_daily_paths(
        normalized_run_date,
        project_root=project_root,
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )

    cycle_directory = (
        daily.cycles_directory
        / normalized_cycle_id
    )

    portfolio_directory = cycle_directory / "portfolio"
    execution_directory = cycle_directory / "execution"
    orders_directory = cycle_directory / "orders"

    return CyclePaths(
        project_root=daily.project_root,
        run_date=daily.run_date,
        cycle_id=normalized_cycle_id,
        profile_id=daily.profile_id,
        strategy_id=daily.strategy_id,
        strategy_version=daily.strategy_version,
        runtime_root=daily.runtime_root,
        identity_root=daily.identity_root,
        day_directory=daily.day_directory,
        daily_state=daily.daily_state,
        coarse_directory=daily.coarse_directory,
        coarse_current=daily.coarse_current,
        coarse_revisions=daily.coarse_revisions,
        coarse_input=daily.coarse_input,
        coarse_output=daily.coarse_output,
        coarse_validation=daily.coarse_validation,
        coarse_codex_call=daily.coarse_codex_call,
        coarse_workspace=daily.coarse_workspace,
        cycles_directory=daily.cycles_directory,
        cycle_directory=cycle_directory,
        cycle_state=cycle_directory / "cycle_state.json",
        base_snapshot=cycle_directory / "base_snapshot.json",
        initial_guidance=(
            cycle_directory / "initial_guidance.json"
        ),
        user_review=cycle_directory / "user_review.json",
        cycle_summary=cycle_directory / "cycle_summary.json",
        portfolio_directory=portfolio_directory,
        portfolio_input=portfolio_directory / "input.json",
        portfolio_output=portfolio_directory / "output.json",
        portfolio_validation=(
            portfolio_directory / "validation.json"
        ),
        portfolio_workspace=portfolio_directory / "workspace",
        execution_directory=execution_directory,
        execution_input=execution_directory / "input.json",
        execution_output=execution_directory / "output.json",
        execution_validation=(
            execution_directory / "validation.json"
        ),
        execution_workspace=execution_directory / "workspace",
        orders_directory=orders_directory,
        proposed_orders=orders_directory / "proposed.json",
        validated_orders=orders_directory / "validated.json",
        broker_submission=orders_directory / "broker_submission.json",
        reconciliation=orders_directory / "reconciliation.json",
        daily_report=daily.daily_report,
    )


def build_coarse_revision_paths(
    paths: DailyPaths | CyclePaths,
    input_signature: str,
) -> CoarseRevisionPaths:
    signature = str(input_signature).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ValueError(
            "coarse input_signature必须是64位SHA-256"
        )
    root = paths.coarse_revisions / signature
    return CoarseRevisionPaths(
        input_signature=signature,
        revision_directory=root,
        input=root / "input.json",
        output=root / "output.json",
        validation=root / "validation.json",
        codex_call=root / "codex_call.json",
        workspace=root / "workspace",
    )


def ensure_daily_directories(paths: DailyPaths) -> None:
    """
    创建日期级目录，但不创建任何状态文件。
    """
    for directory in (
        paths.runtime_root,
        paths.identity_root,
        paths.day_directory,
        paths.coarse_directory,
        paths.coarse_revisions,
        paths.cycles_directory,
        paths.daily_report.parent,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def ensure_cycle_directories(paths: CyclePaths) -> None:
    """
    创建完整轮次目录，但不创建任何状态文件。
    """
    daily_paths = build_daily_paths(
        paths.run_date,
        project_root=paths.project_root,
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
    )
    ensure_daily_directories(daily_paths)

    for directory in (
        paths.cycle_directory,
        paths.portfolio_directory,
        paths.portfolio_workspace,
        paths.execution_directory,
        paths.execution_workspace,
        paths.orders_directory,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def ensure_shared_data_directories(
    paths: SharedDataPaths,
) -> None:
    for directory in (
        paths.root,
        paths.universe,
        paths.market,
        paths.daily,
        paths.intraday,
        paths.quotes,
        paths.assets,
        paths.public_research_cache,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def list_cycle_ids(
    run_date: str | date | datetime | None = None,
    *,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
) -> list[str]:
    """
    返回指定纽约日期下所有合法轮次ID。
    """
    paths = build_daily_paths(
        run_date,
        project_root=project_root,
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )

    if not paths.cycles_directory.exists():
        return []

    cycle_ids: list[str] = []

    for child in paths.cycles_directory.iterdir():
        if not child.is_dir():
            continue

        try:
            normalize_cycle_id(child.name)
        except (TypeError, ValueError):
            continue

        if (
            cycle_id_to_run_date(child.name)
            == paths.run_date
        ):
            cycle_ids.append(child.name)

    return sorted(cycle_ids)


def find_latest_cycle_id(
    run_date: str | date | datetime | None = None,
    *,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
) -> str | None:
    """
    返回指定日期最新轮次ID。
    """
    cycle_ids = list_cycle_ids(
        run_date,
        project_root=project_root,
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )

    return cycle_ids[-1] if cycle_ids else None


def allocate_cycle_id(
    run_date: str | date | datetime | None = None,
    *,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
    now: datetime | None = None,
    max_attempts: int = 120,
) -> str:
    """
    分配唯一轮次ID。

    若同一秒已存在轮次，则逐秒向后寻找空闲ID，
    保持固定格式 YYYYMMDDTHHMMSS。
    """
    if max_attempts <= 0:
        raise ValueError("max_attempts必须大于0")

    normalized_run_date = normalize_run_date(run_date)

    current = now or get_new_york_now()

    if current.tzinfo is None:
        current = current.replace(tzinfo=NEW_YORK_TZ)
    else:
        current = current.astimezone(NEW_YORK_TZ)

    if current.date().isoformat() != normalized_run_date:
        parsed_date = date.fromisoformat(normalized_run_date)
        current = datetime.combine(
            parsed_date,
            current.timetz(),
        )

    for offset in range(max_attempts):
        candidate_time = current + timedelta(seconds=offset)
        candidate_id = generate_cycle_id(candidate_time)

        candidate_paths = build_cycle_paths(
            cycle_id=candidate_id,
            run_date=normalized_run_date,
            project_root=project_root,
            profile_id=profile_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )

        if not candidate_paths.cycle_directory.exists():
            return candidate_id

    raise RuntimeError(
        "无法分配唯一cycle_id；"
        f"已尝试{max_attempts}个连续秒"
    )


def create_cycle_paths(
    run_date: str | date | datetime | None = None,
    *,
    project_root: Path | None = None,
    profile_id: str = "default",
    strategy_id: str = "core_long",
    strategy_version: str = "1.0.0",
    now: datetime | None = None,
) -> CyclePaths:
    """
    分配新轮次ID并创建轮次目录。
    """
    normalized_run_date = normalize_run_date(run_date)

    daily_paths = build_daily_paths(
        normalized_run_date,
        project_root=project_root,
        profile_id=profile_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )
    ensure_daily_directories(daily_paths)

    current = now or get_new_york_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=NEW_YORK_TZ)
    else:
        current = current.astimezone(NEW_YORK_TZ)

    if current.date().isoformat() != normalized_run_date:
        current = datetime.combine(
            date.fromisoformat(normalized_run_date),
            current.timetz(),
        )

    for offset in range(120):
        cycle_id = generate_cycle_id(
            current + timedelta(seconds=offset)
        )
        paths = build_cycle_paths(
            cycle_id=cycle_id,
            run_date=normalized_run_date,
            project_root=project_root,
            profile_id=profile_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )

        try:
            paths.cycle_directory.mkdir(
                parents=False,
                exist_ok=False,
            )
        except FileExistsError:
            continue

        ensure_cycle_directories(paths)
        return paths

    raise RuntimeError(
        "无法原子创建唯一cycle_id；"
        "已尝试120个连续秒"
    )


def load_json_object(path: Path) -> dict[str, Any]:
    """
    读取JSON对象。

    不允许顶层为数组或其他类型。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"JSON文件不存在：{path}"
        )

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"JSON格式损坏：{path}；{error}"
        ) from error

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON顶层必须是对象：{path}"
        )

    return payload


def load_optional_json_object(
    path: Path,
) -> dict[str, Any] | None:
    """
    文件不存在时返回None。
    """
    if not path.exists():
        return None

    return load_json_object(path)


def atomic_write_text(
    path: Path,
    content: str,
) -> None:
    """
    在同一目录内原子写入文本。
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        os.replace(
            temporary_path,
            path,
        )

        # Persist the directory entry as well as the file contents.  This is
        # best-effort because some filesystems do not permit directory fsync.
        try:
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY,
            )
        except OSError:
            directory_fd = None

        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    except Exception:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        finally:
            raise


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """
    原子写入JSON对象。
    """
    if not isinstance(payload, dict):
        raise TypeError(
            "atomic_write_json只接受dict"
        )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ) + "\n"

    atomic_write_text(
        path,
        serialized,
    )


def utc_now_iso() -> str:
    """
    返回带时区的UTC ISO时间。
    """
    return datetime.now(
        ZoneInfo("UTC")
    ).isoformat()


def new_york_now_iso() -> str:
    """
    返回带时区的纽约ISO时间。
    """
    return get_new_york_now().isoformat()


def _print_daily_paths(paths: DailyPaths) -> None:
    print(f"纽约日期：{paths.run_date}")
    print(f"项目根目录：{paths.project_root}")
    print(f"运行根目录：{paths.runtime_root}")
    print(f"日期目录：{paths.day_directory}")
    print(f"日期状态：{paths.daily_state}")
    print(f"粗选目录：{paths.coarse_directory}")
    print(f"粗选输出：{paths.coarse_output}")
    print(f"轮次目录：{paths.cycles_directory}")
    print(f"日报：{paths.daily_report}")


def _print_cycle_paths(paths: CyclePaths) -> None:
    print(f"cycle_id：{paths.cycle_id}")
    print(f"轮次目录：{paths.cycle_directory}")
    print(f"轮次状态：{paths.cycle_state}")
    print(f"组合目录：{paths.portfolio_directory}")
    print(f"执行目录：{paths.execution_directory}")
    print(f"订单目录：{paths.orders_directory}")


def main() -> int:
    """
    runtime.py自检入口。

    默认只显示路径。
    使用 --create-cycle 时创建一个空轮次目录。
    """
    parser = argparse.ArgumentParser(
        description="WA Trader v2运行时路径自检"
    )

    parser.add_argument(
        "--run-date",
        help="纽约日期YYYY-MM-DD",
    )

    parser.add_argument(
        "--create-cycle",
        action="store_true",
        help="创建一个新的空轮次目录",
    )

    arguments = parser.parse_args()

    print(f"脚本版本：{SCRIPT_VERSION}")

    try:
        run_date = normalize_run_date(
            arguments.run_date
        )

        daily_paths = build_daily_paths(
            run_date
        )
        ensure_daily_directories(
            daily_paths
        )
        _print_daily_paths(
            daily_paths
        )

        latest_cycle_id = find_latest_cycle_id(
            run_date
        )

        print(
            "最近轮次："
            f"{latest_cycle_id or '无'}"
        )

        if arguments.create_cycle:
            cycle_paths = create_cycle_paths(
                run_date
            )

            print()
            print("新轮次创建成功")
            _print_cycle_paths(
                cycle_paths
            )

        return 0

    except Exception as error:
        print("runtime.py自检失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
