import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fetch_account import save_json_atomically
from runtime_paths import (
    ensure_run_directory,
    get_new_york_now,
)


SCRIPT_VERSION = "2026-07-22-runtime-paths-v3"


COARSE_INPUT_PATH = (
    Path("data")
    / "snapshots"
    / "coarse_universe_input.json"
)

SOURCE_DAILY_DIRECTORY = (
    Path("data")
    / "bars"
    / "daily"
)


# 同一天刷新工作区时只替换这些由Python管理的输入。
MANAGED_INPUT_NAMES = (
    "data",
    "config",
    "prompts",
    "schemas",
    "AGENTS.md",
)

# 这些路径属于运行结果或研究资产，绝不能因刷新输入而删除。
PERSISTENT_WORKSPACE_NAMES = (
    "output",
    "reports",
    "research",
    "tools",
)


def load_json(
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
            f"JSON顶层不是对象：{path}"
        )

    return payload


def copy_if_exists(
    source: Path,
    destination: Path,
) -> bool:
    """复制存在的文件。"""
    if not source.exists():
        return False

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        source,
        destination,
    )

    return True


def make_read_only(
    path: Path,
) -> None:
    """将复制到工作区的数据文件设为只读。"""
    try:
        os.chmod(path, 0o444)
    except OSError:
        # 权限设置失败不阻断工作区构建；
        # Codex仍受AGENTS和沙箱规则约束。
        pass


def normalize_symbol(
    value: Any,
) -> str:
    """标准化股票代码。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def collect_review_symbols(
    coarse_input: dict[str, Any],
) -> list[str]:
    """读取第一次Codex允许研究的完整标的范围。"""
    records = coarse_input.get(
        "codex_review_universe",
        [],
    )

    if not isinstance(records, list):
        raise ValueError(
            "codex_review_universe必须是数组"
        )

    symbols: list[str] = []
    seen: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)
        symbols.append(symbol)

    if not symbols:
        raise ValueError(
            "Codex可研究标的范围为空"
        )

    return symbols


def create_directories(
    workspace: Path,
) -> None:
    """创建第一次Codex调用使用的目录结构。"""
    directories = [
        workspace / "data" / "snapshots",
        workspace / "data" / "raw_bars" / "daily",
        workspace / "config",
        workspace / "prompts",
        workspace / "schemas",
        workspace / "output",
        workspace / "reports",
        workspace / "research" / "news",
        workspace / "research" / "companies",
        workspace / "research" / "macro",
        workspace / "tools" / "generated",
        workspace / ".tmp" / "codex",
    ]

    for directory in directories:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def copy_snapshot_files(
    project_root: Path,
    workspace: Path,
) -> list[str]:
    """复制第一次Codex可能需要的账户和市场快照。"""
    filenames = [
        "account.json",
        "assets.json",
        "positions.json",
        "open_orders.json",
        "today_orders.json",
        "decision_input.json",
        "coarse_universe_input.json",
    ]

    copied: list[str] = []

    for filename in filenames:
        source = (
            project_root
            / "data"
            / "snapshots"
            / filename
        )

        destination = (
            workspace
            / "data"
            / "snapshots"
            / filename
        )

        if copy_if_exists(
            source,
            destination,
        ):
            make_read_only(destination)
            copied.append(filename)

    required = {
        "account.json",
        "assets.json",
        "positions.json",
        "open_orders.json",
        "decision_input.json",
        "coarse_universe_input.json",
    }

    missing_required = sorted(
        required - set(copied)
    )

    if missing_required:
        raise FileNotFoundError(
            "缺少第一次Codex所需快照："
            + ", ".join(missing_required)
        )

    return copied


def copy_daily_bars(
    project_root: Path,
    workspace: Path,
    symbols: list[str],
) -> tuple[list[str], list[str]]:
    """复制Codex允许研究标的的300根日线。"""
    copied: list[str] = []
    missing: list[str] = []

    source_directory = (
        project_root
        / SOURCE_DAILY_DIRECTORY
    )

    destination_directory = (
        workspace
        / "data"
        / "raw_bars"
        / "daily"
    )

    for symbol in symbols:
        source = (
            source_directory
            / f"{symbol}.json"
        )

        destination = (
            destination_directory
            / f"{symbol}.json"
        )

        if not source.exists():
            missing.append(symbol)
            continue

        shutil.copy2(
            source,
            destination,
        )

        make_read_only(destination)
        copied.append(symbol)

    return copied, missing


def copy_support_files(
    project_root: Path,
    workspace: Path,
) -> list[str]:
    """复制安全策略、提示词和Schema。"""
    candidates = [
        (
            project_root
            / "config"
            / "daily_decision_policy.json",
            workspace
            / "config"
            / "daily_decision_policy.json",
        ),
        (
            project_root
            / "config"
            / "screener.json",
            workspace
            / "config"
            / "screener.json",
        ),
        (
            project_root
            / "config"
            / "order_policy.json",
            workspace
            / "config"
            / "order_policy.json",
        ),
        (
            project_root
            / "schemas"
            / "coarse_candidates.schema.json",
            workspace
            / "schemas"
            / "coarse_candidates.schema.json",
        ),
        (
            project_root
            / "schemas"
            / "order_plan.schema.json",
            workspace
            / "schemas"
            / "order_plan.schema.json",
        ),
        (
            project_root
            / "prompts"
            / "coarse_selection.md",
            workspace
            / "prompts"
            / "coarse_selection.md",
        ),
        (
            project_root
            / "prompts"
            / "position_decision.md",
            workspace
            / "prompts"
            / "position_decision.md",
        ),
    ]

    copied: list[str] = []

    for source, destination in candidates:
        if copy_if_exists(
            source,
            destination,
        ):
            make_read_only(destination)
            copied.append(
                str(
                    destination.relative_to(
                        workspace
                    )
                )
            )

    # 后续会换成粗选阶段专用AGENTS.md。
    # 当前优先使用项目根目录下的粗选版本；不存在时才兼容旧工作区规则。
    agents_candidates = [
        project_root
        / "prompts"
        / "coarse_selection_AGENTS.md",
        project_root
        / "decision_workspace"
        / "AGENTS.md",
    ]

    destination_agents = (
        workspace
        / "AGENTS.md"
    )

    for source in agents_candidates:
        if copy_if_exists(
            source,
            destination_agents,
        ):
            make_read_only(
                destination_agents
            )
            copied.append("AGENTS.md")
            break

    return copied


def build_manifest(
    *,
    run_date: str,
    workspace: Path,
    source_generated_at: Any,
    review_symbols: list[str],
    copied_daily_symbols: list[str],
    copied_snapshots: list[str],
    copied_support_files: list[str],
    workspace_update_mode: str,
) -> dict[str, Any]:
    """生成粗选工作区清单。"""
    return {
        "schema_version": "1.1",
        "stage": "coarse_selection",
        "run_date": run_date,
        "generated_at": (
            get_new_york_now().isoformat()
        ),
        "source_generated_at": (
            source_generated_at
        ),
        "workspace": str(workspace),
        "decision_state_path": str(
            workspace.parent
            / "decision_state.json"
        ),
        "workspace_update_mode": (
            workspace_update_mode
        ),
        "review_symbol_count": len(
            review_symbols
        ),
        "daily_file_count": len(
            copied_daily_symbols
        ),
        "review_symbols": review_symbols,
        "copied_snapshots": (
            copied_snapshots
        ),
        "copied_support_files": (
            copied_support_files
        ),
        "preserved_on_same_day_refresh": [
            "output/",
            "reports/",
            "research/",
            "tools/",
            "../decision_state.json",
        ],
        "allowed_writes": [
            "output/",
            "reports/",
            "research/",
            "tools/generated/",
            ".tmp/codex/",
        ],
        "forbidden_data": [
            ".env",
            "Alpaca API keys",
            "production order executor",
            "project source outside workspace",
        ],
        "expected_stage_output": (
            "output/coarse_candidates.json"
        ),
        "notes": [
            (
                "第一次Codex必须从完整合格范围中"
                "自主选择最终60只。"
            ),
            (
                "priority_score_reference仅供安排"
                "阅读顺序，不得直接截取前60只。"
            ),
            (
                "市场和行业平衡仅为Prompt软约束，"
                "Python不得据此硬性否决候选。"
            ),
            (
                "复制到data/下的输入文件应视为只读。"
            ),
            (
                "同日刷新仅替换Python管理的输入，"
                "不得删除已有输出、研究和报告。"
            ),
        ],
    }


def remove_path(
    path: Path,
) -> None:
    """删除文件、符号链接或目录。"""
    if not path.exists() and not path.is_symlink():
        return

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def install_staged_workspace(
    *,
    staging_workspace: Path,
    workspace: Path,
) -> str:
    """
    安装已完整构建的临时工作区。

    新建日期：直接重命名整个目录。
    同日刷新：只替换输入与契约，保留持久结果目录。
    """
    if not workspace.exists():
        os.replace(
            staging_workspace,
            workspace,
        )
        return "created"

    workspace.mkdir(
        parents=True,
        exist_ok=True,
    )

    for persistent_name in (
        PERSISTENT_WORKSPACE_NAMES
    ):
        (
            workspace
            / persistent_name
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

    for name in MANAGED_INPUT_NAMES:
        staged_path = (
            staging_workspace
            / name
        )

        if not staged_path.exists():
            continue

        destination = workspace / name
        backup = (
            workspace
            / f".{name.replace('/', '_')}"
            ".refresh_backup"
        )

        remove_path(backup)

        if destination.exists():
            os.replace(
                destination,
                backup,
            )

        try:
            os.replace(
                staged_path,
                destination,
            )
        except Exception:
            if backup.exists():
                os.replace(
                    backup,
                    destination,
                )
            raise
        else:
            remove_path(backup)

    # 临时帮助代码不能跨调用残留。
    temporary_directory = (
        workspace
        / ".tmp"
        / "codex"
    )
    remove_path(temporary_directory)
    temporary_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    remove_path(staging_workspace)

    return "refreshed_preserving_outputs"


def prepare_coarse_workspace() -> Path:
    """
    创建或安全刷新第一次Codex调用的隔离工作区。

    工作区不包含密钥或交易执行器。
    同一纽约日期再次运行时，不删除既有输出、研究、报告和状态。
    """
    paths = ensure_run_directory()
    project_root = paths.project_root

    coarse_input = load_json(
        project_root / COARSE_INPUT_PATH
    )

    review_symbols = (
        collect_review_symbols(
            coarse_input
        )
    )

    run_date = paths.run_date
    runtime_date_directory = (
        paths.run_directory
    )
    workspace = paths.coarse_workspace

    staging_workspace = (
        runtime_date_directory
        / (
            ".coarse_workspace_staging_"
            + uuid.uuid4().hex
        )
    )

    create_directories(
        staging_workspace
    )

    try:
        copied_snapshots = (
            copy_snapshot_files(
                project_root=project_root,
                workspace=staging_workspace,
            )
        )

        (
            copied_daily_symbols,
            missing_daily_symbols,
        ) = copy_daily_bars(
            project_root=project_root,
            workspace=staging_workspace,
            symbols=review_symbols,
        )

        if missing_daily_symbols:
            raise FileNotFoundError(
                "以下标的缺少日线文件："
                + ", ".join(
                    missing_daily_symbols[:20]
                )
                + (
                    f"；另有"
                    f"{len(missing_daily_symbols) - 20}"
                    "只"
                    if len(missing_daily_symbols) > 20
                    else ""
                )
            )

        copied_support_files = (
            copy_support_files(
                project_root=project_root,
                workspace=staging_workspace,
            )
        )

        # 在临时区先写入清单，保证输入构建完整后才替换正式目录。
        preliminary_mode = (
            "refreshed_preserving_outputs"
            if workspace.exists()
            else "created"
        )

        manifest = build_manifest(
            run_date=run_date,
            workspace=workspace,
            source_generated_at=(
                coarse_input.get(
                    "generated_at"
                )
            ),
            review_symbols=review_symbols,
            copied_daily_symbols=(
                copied_daily_symbols
            ),
            copied_snapshots=(
                copied_snapshots
            ),
            copied_support_files=(
                copied_support_files
            ),
            workspace_update_mode=(
                preliminary_mode
            ),
        )

        manifest_path = (
            staging_workspace
            / "data"
            / "workspace_manifest.json"
        )

        save_json_atomically(
            manifest_path,
            manifest,
        )

        make_read_only(
            manifest_path
        )

        installed_mode = (
            install_staged_workspace(
                staging_workspace=(
                    staging_workspace
                ),
                workspace=workspace,
            )
        )

        if installed_mode != preliminary_mode:
            raise RuntimeError(
                "工作区安装模式异常："
                f"{installed_mode}"
            )

        return workspace

    except Exception:
        remove_path(
            staging_workspace
        )
        raise


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    try:
        workspace = (
            prepare_coarse_workspace()
        )

        manifest = load_json(
            workspace
            / "data"
            / "workspace_manifest.json"
        )

        print("粗选工作区准备成功")
        print(f"工作区：{workspace}")
        print(
            "更新模式："
            f"{manifest.get('workspace_update_mode')}"
        )
        print(
            "Codex可研究标的："
            f"{manifest.get('review_symbol_count', 0)}"
        )
        print(
            "复制日线文件："
            f"{manifest.get('daily_file_count', 0)}"
        )
        print(
            "预期输出："
            f"{manifest.get('expected_stage_output')}"
        )
        print(
            "同日保留：output、research、"
            "reports、tools和decision_state"
        )

        return 0

    except Exception as error:
        print("粗选工作区准备失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())