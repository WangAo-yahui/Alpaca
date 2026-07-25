import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fetch_account import save_json_atomically
from runtime_paths import (
    build_runtime_paths,
    get_project_root,
)


SCRIPT_VERSION = (
    "2026-07-22-contract-readiness-fix-v2"
)

PORTFOLIO_INPUT_RELATIVE_PATH = (
    Path("data")
    / "snapshots"
    / "portfolio_input.json"
)

MANAGED_INPUT_NAMES = (
    "data",
    "config",
    "prompts",
    "schemas",
    "AGENTS.md",
)

PERSISTENT_WORKSPACE_NAMES = (
    "output",
    "reports",
    "research",
    "tools",
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


def normalize_symbol(
    value: Any,
) -> str:
    """标准化股票代码。"""
    if not isinstance(value, str):
        return ""

    return value.strip().upper()


def collect_candidate_symbols(
    portfolio_input: dict[str, Any],
) -> list[str]:
    """从第二阶段统一输入读取60只候选。"""
    candidates = portfolio_input.get(
        "candidates",
        [],
    )

    if not isinstance(candidates, list):
        raise ValueError(
            "portfolio_input.candidates必须是数组"
        )

    symbols: list[str] = []
    seen: set[str] = set()

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

        if symbol in seen:
            raise ValueError(
                f"portfolio_input存在重复标的："
                f"{symbol}"
            )

        seen.add(symbol)
        symbols.append(symbol)

    if len(symbols) != 60:
        raise ValueError(
            "第二阶段工作区要求恰好60只候选，"
            f"实际={len(symbols)}"
        )

    return symbols


def make_read_only(
    path: Path,
) -> None:
    """将工作区输入设为只读。"""
    try:
        os.chmod(path, 0o444)
    except OSError:
        pass


def copy_file_read_only(
    source: Path,
    destination: Path,
    *,
    required: bool,
) -> bool:
    """复制文件并设为只读。"""
    if not source.exists():
        if required:
            raise FileNotFoundError(
                f"缺少文件：{source}"
            )

        return False

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        source,
        destination,
    )

    make_read_only(destination)

    return True


def create_workspace_directories(
    workspace: Path,
    run_date: str,
) -> None:
    """创建第二阶段工作区目录。"""
    directories = [
        workspace
        / "data"
        / "snapshots",
        workspace
        / "data"
        / "raw_bars"
        / "daily",
        workspace
        / "data"
        / "raw_bars"
        / "intraday"
        / run_date,
        workspace / "config",
        workspace / "prompts",
        workspace / "schemas",
        workspace / "output",
        workspace / "reports",
        workspace / "research",
        workspace / "tools",
        workspace / ".tmp" / "codex",
    ]

    for directory in directories:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def copy_snapshot_inputs(
    *,
    project_root: Path,
    coarse_workspace: Path,
    staging_workspace: Path,
) -> list[str]:
    """复制第二阶段统一输入及可追溯快照。"""
    mappings = [
        (
            project_root
            / PORTFOLIO_INPUT_RELATIVE_PATH,
            staging_workspace
            / "data"
            / "snapshots"
            / "portfolio_input.json",
            True,
        ),
        (
            coarse_workspace
            / "output"
            / "coarse_candidates.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "coarse_candidates.json",
            True,
        ),
        (
            project_root
            / "data"
            / "snapshots"
            / "account.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "account.json",
            True,
        ),
        (
            project_root
            / "data"
            / "snapshots"
            / "positions.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "positions.json",
            True,
        ),
        (
            project_root
            / "data"
            / "snapshots"
            / "open_orders.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "open_orders.json",
            True,
        ),
        (
            project_root
            / "data"
            / "snapshots"
            / "today_orders.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "today_orders.json",
            False,
        ),
        (
            project_root
            / "data"
            / "snapshots"
            / "assets.json",
            staging_workspace
            / "data"
            / "snapshots"
            / "assets.json",
            False,
        ),
    ]

    copied: list[str] = []

    for source, destination, required in mappings:
        if copy_file_read_only(
            source,
            destination,
            required=required,
        ):
            copied.append(
                str(
                    destination.relative_to(
                        staging_workspace
                    )
                )
            )

    return copied


def copy_raw_market_data(
    *,
    project_root: Path,
    staging_workspace: Path,
    run_date: str,
    symbols: list[str],
) -> tuple[list[str], list[str]]:
    """复制60只候选的原始日线和盘中快照。"""
    copied_daily: list[str] = []
    copied_intraday: list[str] = []

    for symbol in symbols:
        daily_source = (
            project_root
            / "data"
            / "bars"
            / "daily"
            / f"{symbol}.json"
        )
        daily_destination = (
            staging_workspace
            / "data"
            / "raw_bars"
            / "daily"
            / f"{symbol}.json"
        )

        copy_file_read_only(
            daily_source,
            daily_destination,
            required=True,
        )
        copied_daily.append(symbol)

        intraday_source = (
            project_root
            / "data"
            / "bars"
            / "intraday"
            / run_date
            / f"{symbol}.json"
        )
        intraday_destination = (
            staging_workspace
            / "data"
            / "raw_bars"
            / "intraday"
            / run_date
            / f"{symbol}.json"
        )

        copy_file_read_only(
            intraday_source,
            intraday_destination,
            required=True,
        )
        copied_intraday.append(symbol)

    return copied_daily, copied_intraday


def copy_support_files(
    *,
    project_root: Path,
    staging_workspace: Path,
) -> list[str]:
    """
    复制当前已存在的第二阶段支持文件。

    第二阶段专用Prompt、Schema和AGENTS会在后续步骤创建。
    在它们存在之前，不应启动第二次Codex调用。
    """
    mappings = [
        (
            project_root
            / "config"
            / "daily_decision_policy.json",
            staging_workspace
            / "config"
            / "daily_decision_policy.json",
        ),
        (
            project_root
            / "config"
            / "order_policy.json",
            staging_workspace
            / "config"
            / "order_policy.json",
        ),
        (
            project_root
            / "config"
            / "screener.json",
            staging_workspace
            / "config"
            / "screener.json",
        ),
        (
            project_root
            / "prompts"
            / "position_decision.md",
            staging_workspace
            / "prompts"
            / "position_decision.md",
        ),
        (
            project_root
            / "prompts"
            / "portfolio_decision.md",
            staging_workspace
            / "prompts"
            / "portfolio_decision.md",
        ),
        (
            project_root
            / "prompts"
            / "portfolio_decision_AGENTS.md",
            staging_workspace
            / "AGENTS.md",
        ),
        (
            project_root
            / "schemas"
            / "portfolio_decision.schema.json",
            staging_workspace
            / "schemas"
            / "portfolio_decision.schema.json",
        ),
    ]

    copied: list[str] = []

    for source, destination in mappings:
        if copy_file_read_only(
            source,
            destination,
            required=False,
        ):
            copied.append(
                str(
                    destination.relative_to(
                        staging_workspace
                    )
                )
            )

    return copied


def remove_path(
    path: Path,
) -> None:
    """删除文件、链接或目录。"""
    if (
        not path.exists()
        and not path.is_symlink()
    ):
        return

    if (
        path.is_dir()
        and not path.is_symlink()
    ):
        shutil.rmtree(path)
    else:
        path.unlink()


def install_staged_workspace(
    *,
    staging_workspace: Path,
    workspace: Path,
) -> str:
    """
    安装临时工作区。

    新日期直接原子重命名；
    同日只替换输入，保留已有输出。
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

    for name in PERSISTENT_WORKSPACE_NAMES:
        (
            workspace / name
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

    for name in MANAGED_INPUT_NAMES:
        staged_path = (
            staging_workspace / name
        )

        if not staged_path.exists():
            continue

        destination = workspace / name
        backup = (
            workspace
            / (
                "."
                + name.replace("/", "_")
                + ".refresh_backup"
            )
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


def inspect_stage_contracts(
    workspace: Path,
) -> tuple[bool, list[str]]:
    """
    在临时工作区安装前检查第二阶段契约。

    必须检查staging_workspace，而不是尚未替换的正式工作区。
    """
    required_paths = (
        Path(
            "prompts/"
            "portfolio_decision.md"
        ),
        Path(
            "schemas/"
            "portfolio_decision.schema.json"
        ),
        Path("AGENTS.md"),
    )

    missing = [
        str(relative_path)
        for relative_path in required_paths
        if not (
            workspace / relative_path
        ).is_file()
    ]

    return not missing, missing


def build_manifest(
    *,
    run_date: str,
    workspace: Path,
    decision_state_path: Path,
    portfolio_input: dict[str, Any],
    symbols: list[str],
    copied_snapshots: list[str],
    copied_daily: list[str],
    copied_intraday: list[str],
    copied_support: list[str],
    update_mode: str,
    stage_contract_ready: bool,
    missing_stage_contracts: list[str],
) -> dict[str, Any]:
    """生成第二阶段工作区清单。"""
    market_data_status = (
        portfolio_input.get(
            "market_data_status",
            {},
        )
    )

    if not isinstance(
        market_data_status,
        dict,
    ):
        market_data_status = {}

    return {
        "schema_version": "1.0",
        "stage": "portfolio_decision",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "run_date": run_date,
        "workspace": str(workspace),
        "decision_state_path": str(
            decision_state_path
        ),
        "workspace_update_mode": (
            update_mode
        ),
        "candidate_count": len(symbols),
        "candidate_symbols": symbols,
        "copied_snapshot_files": (
            copied_snapshots
        ),
        "copied_daily_count": len(
            copied_daily
        ),
        "copied_intraday_count": len(
            copied_intraday
        ),
        "copied_support_files": (
            copied_support
        ),
        "intraday_status": {
            "success_count": (
                market_data_status.get(
                    "intraday_success_count"
                )
            ),
            "no_data_count": (
                market_data_status.get(
                    "intraday_no_data_count"
                )
            ),
            "failed_count": (
                market_data_status.get(
                    "intraday_failed_count"
                )
            ),
            "window_statuses": (
                market_data_status.get(
                    "intraday_window_statuses",
                    [],
                )
            ),
            "no_data_is_not_zero": True,
        },
        "input_access_strategy": {
            "summary_first": (
                "data/snapshots/"
                "portfolio_input.json"
            ),
            "drill_down_daily": (
                "data/raw_bars/daily/"
                "{symbol}.json"
            ),
            "drill_down_intraday": (
                "data/raw_bars/intraday/"
                f"{run_date}/"
                "{symbol}.json"
            ),
        },
        "preserved_on_same_day_refresh": [
            "output/",
            "reports/",
            "research/",
            "tools/",
            "../decision_state.json",
        ],
        "expected_stage_output": (
            "output/portfolio_decision.json"
        ),
        "stage_contract_ready": (
            stage_contract_ready
        ),
        "missing_stage_contracts": (
            missing_stage_contracts
        ),
        "notes": [
            (
                "本工作区只包含已验证的60只"
                "粗选候选及账户上下文。"
            ),
            (
                "盘前盘中no_data只表示延迟数据"
                "尚不可用，不代表价格或成交量为0。"
            ),
            (
                "第二阶段可以提出目标组合与风险"
                "控制，但不能授予最终开仓权限。"
            ),
            (
                "在Prompt、Schema和AGENTS全部"
                "就绪前不得调用第二阶段Codex。"
            ),
        ],
    }


def prepare_portfolio_workspace() -> Path:
    """创建或安全刷新第二阶段隔离工作区。"""
    project_root = get_project_root()

    portfolio_input_path = (
        project_root
        / PORTFOLIO_INPUT_RELATIVE_PATH
    )

    portfolio_input = load_json_object(
        portfolio_input_path
    )

    run_date = portfolio_input.get(
        "run_date"
    )

    if not isinstance(run_date, str):
        raise ValueError(
            "portfolio_input缺少run_date"
        )

    runtime_paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    coarse_workspace = (
        runtime_paths.coarse_workspace
    )

    if not coarse_workspace.exists():
        raise FileNotFoundError(
            "缺少对应日期的粗选工作区："
            f"{coarse_workspace}"
        )

    symbols = collect_candidate_symbols(
        portfolio_input
    )

    runtime_paths.run_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    workspace = (
        runtime_paths.portfolio_workspace
    )

    staging_workspace = (
        runtime_paths.run_directory
        / (
            ".portfolio_workspace_staging_"
            + uuid.uuid4().hex
        )
    )

    create_workspace_directories(
        staging_workspace,
        run_date,
    )

    try:
        copied_snapshots = (
            copy_snapshot_inputs(
                project_root=project_root,
                coarse_workspace=(
                    coarse_workspace
                ),
                staging_workspace=(
                    staging_workspace
                ),
            )
        )

        (
            copied_daily,
            copied_intraday,
        ) = copy_raw_market_data(
            project_root=project_root,
            staging_workspace=(
                staging_workspace
            ),
            run_date=run_date,
            symbols=symbols,
        )

        copied_support = copy_support_files(
            project_root=project_root,
            staging_workspace=(
                staging_workspace
            ),
        )

        update_mode = (
            "refreshed_preserving_outputs"
            if workspace.exists()
            else "created"
        )

        (
            stage_contract_ready,
            missing_stage_contracts,
        ) = inspect_stage_contracts(
            staging_workspace
        )

        manifest = build_manifest(
            run_date=run_date,
            workspace=workspace,
            decision_state_path=(
                runtime_paths.decision_state
            ),
            portfolio_input=(
                portfolio_input
            ),
            symbols=symbols,
            copied_snapshots=(
                copied_snapshots
            ),
            copied_daily=copied_daily,
            copied_intraday=(
                copied_intraday
            ),
            copied_support=(
                copied_support
            ),
            update_mode=update_mode,
            stage_contract_ready=(
                stage_contract_ready
            ),
            missing_stage_contracts=(
                missing_stage_contracts
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
        make_read_only(manifest_path)

        installed_mode = (
            install_staged_workspace(
                staging_workspace=(
                    staging_workspace
                ),
                workspace=workspace,
            )
        )

        if installed_mode != update_mode:
            raise RuntimeError(
                "工作区安装模式异常："
                f"{installed_mode}"
            )

        return workspace

    except Exception:
        remove_path(staging_workspace)
        raise


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    try:
        workspace = (
            prepare_portfolio_workspace()
        )

        manifest = load_json_object(
            workspace
            / "data"
            / "workspace_manifest.json"
        )

        print("第二阶段工作区准备成功")
        print(f"工作区：{workspace}")
        print(
            "更新模式："
            f"{manifest.get('workspace_update_mode')}"
        )
        print(
            "候选数量："
            f"{manifest.get('candidate_count')}"
        )
        print(
            "复制日线文件："
            f"{manifest.get('copied_daily_count')}"
        )
        print(
            "复制盘中文件："
            f"{manifest.get('copied_intraday_count')}"
        )
        print(
            "阶段契约是否就绪："
            f"{manifest.get('stage_contract_ready')}"
        )
        print(
            "预期输出："
            f"{manifest.get('expected_stage_output')}"
        )

        if not manifest.get(
            "stage_contract_ready"
        ):
            missing = manifest.get(
                "missing_stage_contracts",
                [],
            )

            print(
                "注意：第二阶段Prompt、Schema或"
                "AGENTS尚未全部就绪，"
                "暂时不要调用Codex"
            )

            if missing:
                print(
                    "缺少契约："
                    + ", ".join(missing)
                )

        return 0

    except Exception as error:
        print("第二阶段工作区准备失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())