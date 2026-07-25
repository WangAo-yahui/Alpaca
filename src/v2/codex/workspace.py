"""为 Stage C 创建最小化、隔离的 Codex 工作区。

作用：只复制固定 release 中的 prompt、schema、policy 和本次粗选输入。
重要性：它限制 Codex 可读取和写入的范围，避免接触凭据、账户状态及其他轮次产物。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from v2.config import V2Config
from v2.runtime import (
    CoarseRevisionPaths,
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
                config.stages[
                    "coarse_candidate_count"
                ]
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
