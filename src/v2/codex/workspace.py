"""为 Stage C、Stage D 与 Stage E 创建最小化、隔离的 Codex 工作区。

作用：只复制固定 release 中的 prompt、schema、policy 和当前阶段必要输入。
重要性：它限制 Codex 可读取和写入的范围，避免接触凭据、账户绑定及其他身份产物。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from v2.config import V2Config
from v2.runtime import (
    CoarseRevisionPaths,
    CyclePaths,
    DailyPaths,
    atomic_write_json,
    atomic_write_text,
    load_json_object,
)
from v2.releases import StrategyRelease


@dataclass(frozen=True)
class CoarseWorkspace:
    root: Path
    agents: Path
    input_file: Path
    policy_file: Path
    prompt_file: Path
    schema_file: Path
    temp_directory: Path
    last_message: Path
    output_directory: Path


@dataclass(frozen=True)
class PortfolioWorkspace:
    root: Path
    agents: Path
    input_file: Path
    policy_file: Path
    risk_file: Path
    prompt_file: Path
    schema_file: Path
    temp_directory: Path
    last_message: Path


@dataclass(frozen=True)
class ExecutionWorkspace:
    root: Path
    agents: Path
    input_file: Path
    policy_file: Path
    risk_file: Path
    prompt_file: Path
    schema_file: Path
    temp_directory: Path
    last_message: Path


def _read_required(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(
            f"粗选工作区模板不存在：{path}"
        )
    return path.read_text(encoding="utf-8")


def prepare_coarse_workspace(
    paths: DailyPaths | CoarseRevisionPaths,
    *,
    config: V2Config,
    input_payload: Mapping[str, Any],
    release: StrategyRelease | None = None,
) -> CoarseWorkspace:
    root = (
        paths.workspace
        if isinstance(
            paths,
            CoarseRevisionPaths,
        )
        else paths.coarse_workspace
    )
    data_directory = root / "data"
    config_directory = root / "config"
    prompts_directory = root / "prompts"
    schemas_directory = root / "schemas"
    temp_directory = root / ".tmp" / "codex"
    output_directory = root / "output"
    for directory in (
        root,
        data_directory,
        config_directory,
        prompts_directory,
        schemas_directory,
        temp_directory,
        output_directory,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    if release is None:
        source_prompt = (
            config.project_root
            / "prompts"
            / "v2"
            / "coarse.md"
        )
        source_agents = (
            config.project_root
            / "prompts"
            / "v2"
            / "coarse_AGENTS.md"
        )
        source_schema = (
            config.project_root
            / "schemas"
            / "v2"
            / "coarse_output.schema.json"
        )
    else:
        source_prompt = (
            release.root
            / "prompts"
            / "coarse.md"
        )
        source_agents = (
            release.root
            / "prompts"
            / "coarse_AGENTS.md"
        )
        source_schema = (
            release.root
            / "schemas"
            / "coarse_output.schema.json"
        )
    agents = root / "AGENTS.md"
    input_file = data_directory / "input.json"
    policy_file = (
        config_directory
        / "coarse_policy.json"
    )
    prompt_file = (
        prompts_directory / "coarse.md"
    )
    schema_file = (
        schemas_directory
        / "coarse_output.schema.json"
    )
    atomic_write_text(
        agents,
        _read_required(source_agents),
    )
    atomic_write_json(
        input_file,
        dict(input_payload),
    )
    screening = config.stages[
        "coarse_screening"
    ]
    atomic_write_json(
        policy_file,
        {
            **(
                load_json_object(
                    release.root
                    / "config"
                    / "coarse_policy.json"
                )
                if release is not None
                else {}
            ),
            "schema_version": "1.0",
            "stage": "coarse_selection",
            "required_selection_count": int(
                (
                    input_payload.get(
                        "policy",
                        {},
                    ).get(
                        "required_selection_count",
                        config.stages[
                            "coarse_candidate_count"
                        ],
                    )
                    if isinstance(
                        input_payload.get(
                            "policy",
                            {},
                        ),
                        Mapping,
                    )
                    else config.stages[
                        "coarse_candidate_count"
                    ]
                )
            ),
            "stage_version": (
                config.stages[
                    "coarse_stage_version"
                ]
            ),
            "screening_version": (
                config.stages[
                    "coarse_screening_version"
                ]
            ),
            "prompt_version": (
                config.stages[
                    "coarse_prompt_version"
                ]
            ),
            "schema_version_expected": (
                config.stages[
                    "coarse_schema_version"
                ]
            ),
            "screening": dict(screening),
            "forbidden_decision_fields": [
                "new_position_allowed",
                "target_weight",
                "order",
                "orders",
                "quantity",
            ],
        },
    )
    atomic_write_text(
        prompt_file,
        _read_required(source_prompt),
    )
    atomic_write_text(
        schema_file,
        _read_required(source_schema),
    )
    return CoarseWorkspace(
        root=root,
        agents=agents,
        input_file=input_file,
        policy_file=policy_file,
        prompt_file=prompt_file,
        schema_file=schema_file,
        temp_directory=temp_directory,
        last_message=(
            temp_directory
            / "last_message.json"
        ),
        output_directory=output_directory,
    )


def prepare_portfolio_workspace(
    paths: CyclePaths,
    *,
    input_payload: Mapping[str, Any],
    release: StrategyRelease,
    risk_profile_payload: Mapping[str, Any],
) -> PortfolioWorkspace:
    """Create a cycle-local, credential-free Stage D research workspace."""

    root = paths.portfolio_workspace
    data_directory = root / "data"
    market_directory = data_directory / "market"
    config_directory = root / "config"
    prompts_directory = root / "prompts"
    schemas_directory = root / "schemas"
    temp_directory = root / ".tmp" / "codex"
    for directory in (
        root,
        data_directory,
        market_directory,
        config_directory,
        prompts_directory,
        schemas_directory,
        temp_directory,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    agents = root / "AGENTS.md"
    input_file = (
        data_directory / "portfolio_input.json"
    )
    policy_file = (
        config_directory / "portfolio_policy.json"
    )
    risk_file = (
        config_directory / "risk_profile.json"
    )
    prompt_file = (
        prompts_directory / "portfolio.md"
    )
    schema_file = (
        schemas_directory
        / "portfolio_output.schema.json"
    )
    atomic_write_text(
        agents,
        _read_required(
            release.root
            / "prompts"
            / "portfolio_AGENTS.md"
        ),
    )
    atomic_write_json(
        input_file,
        dict(input_payload),
    )
    base_workspace_payload = {
        key: input_payload.get(key)
        for key in (
            "run_date",
            "cycle_id",
            "account",
            "capital",
            "positions",
            "open_orders",
            "data_quality",
            "market_context",
        )
    }
    for filename, payload in (
        (
            "initial_guidance.json",
            input_payload.get(
                "initial_guidance",
                {},
            ),
        ),
        (
            "coarse_output.json",
            input_payload.get("coarse", {}),
        ),
        (
            "base_snapshot.json",
            base_workspace_payload,
        ),
    ):
        if filename == "coarse_output.json" and isinstance(
            payload,
            Mapping,
        ):
            payload = payload.get("output", {})
        atomic_write_json(
            data_directory / filename,
            dict(payload)
            if isinstance(payload, Mapping)
            else {},
        )
    atomic_write_json(
        market_directory / "context.json",
        dict(
            input_payload.get(
                "market_context",
                {},
            )
        )
        if isinstance(
            input_payload.get("market_context"),
            Mapping,
        )
        else {},
    )
    atomic_write_json(
        policy_file,
        load_json_object(
            release.root
            / "config"
            / "portfolio_policy.json"
        ),
    )
    atomic_write_json(
        risk_file,
        dict(risk_profile_payload),
    )
    atomic_write_text(
        prompt_file,
        _read_required(
            release.root
            / "prompts"
            / "portfolio.md"
        ),
    )
    atomic_write_text(
        schema_file,
        _read_required(
            release.root
            / "schemas"
            / "portfolio_output.schema.json"
        ),
    )
    return PortfolioWorkspace(
        root=root,
        agents=agents,
        input_file=input_file,
        policy_file=policy_file,
        risk_file=risk_file,
        prompt_file=prompt_file,
        schema_file=schema_file,
        temp_directory=temp_directory,
        last_message=(
            temp_directory / "last_message.json"
        ),
    )


def prepare_execution_workspace(
    paths: CyclePaths,
    *,
    input_payload: Mapping[str, Any],
    release: StrategyRelease,
) -> ExecutionWorkspace:
    """Create the Stage E workspace without credentials or other profiles."""

    root = paths.execution_workspace
    data_directory = root / "data"
    config_directory = root / "config"
    prompts_directory = root / "prompts"
    schemas_directory = root / "schemas"
    temp_directory = root / ".tmp" / "codex"
    for directory in (
        root,
        data_directory,
        config_directory,
        prompts_directory,
        schemas_directory,
        temp_directory,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )
    agents = root / "AGENTS.md"
    input_file = (
        data_directory / "execution_input.json"
    )
    policy_file = (
        config_directory / "execution_policy.json"
    )
    risk_file = (
        config_directory / "risk_profile.json"
    )
    prompt_file = (
        prompts_directory / "execution.md"
    )
    schema_file = (
        schemas_directory
        / "execution_output.schema.json"
    )
    atomic_write_text(
        agents,
        _read_required(
            release.root
            / "prompts"
            / "execution_AGENTS.md"
        ),
    )
    atomic_write_json(
        input_file,
        dict(input_payload),
    )
    for filename, key in (
        (
            "initial_guidance.json",
            "initial_guidance",
        ),
        ("user_review.json", "user_review"),
        ("portfolio_output.json", "portfolio"),
        (
            "execution_snapshot.json",
            "execution_snapshot",
        ),
    ):
        value = input_payload.get(key, {})
        atomic_write_json(
            data_directory / filename,
            dict(value)
            if isinstance(value, Mapping)
            else {},
        )
    atomic_write_json(
        policy_file,
        load_json_object(
            release.root
            / "config"
            / "execution_policy.json"
        ),
    )
    risk_payload = input_payload.get(
        "risk_profile",
        {},
    )
    atomic_write_json(
        risk_file,
        dict(risk_payload)
        if isinstance(
            risk_payload,
            Mapping,
        )
        else {},
    )
    atomic_write_text(
        prompt_file,
        _read_required(
            release.root
            / "prompts"
            / "execution.md"
        ),
    )
    atomic_write_text(
        schema_file,
        _read_required(
            release.root
            / "schemas"
            / "execution_output.schema.json"
        ),
    )
    return ExecutionWorkspace(
        root=root,
        agents=agents,
        input_file=input_file,
        policy_file=policy_file,
        risk_file=risk_file,
        prompt_file=prompt_file,
        schema_file=schema_file,
        temp_directory=temp_directory,
        last_message=(
            temp_directory
            / "last_message.json"
        ),
    )
