"""编排 Stage C 粗选的 revision、Codex、校验和原子安装。

作用：按 input signature 复用或创建 revision，并用 current.json 指向最近有效结果。
重要性：失败重跑不得覆盖旧有效输出，且本阶段绝不能产生组合决策或订单产物。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from v2.cli import CLIOptions
from v2.codex.runner import (
    CodexRunResult,
    CodexRunner,
    codex_runner_settings,
)
from v2.codex.validation import (
    load_coarse_schema,
    preflight_output_schema,
    validate_coarse_output,
)
from v2.codex.workspace import (
    CoarseWorkspace,
    prepare_coarse_workspace,
)
from v2.config import V2Config
from v2.data.daily_bars import DailyBarStore
from v2.exceptions import (
    CodexOutputValidationError,
    TemporaryDataError,
)
from v2.guidance import load_initial_guidance
from v2.models.coarse import (
    CoarseInputBuildResult,
    CoarseValidationResult,
    build_coarse_input,
)
from v2.models.state import (
    CoarseStatus,
    CycleState,
    DailyState,
    load_daily_state,
    save_daily_state,
)
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
    build_coarse_revision_paths,
    build_daily_paths,
    load_json_object,
    utc_now_iso,
)
from v2.releases import (
    StrategyRelease,
    load_strategy_release,
)


class CoarseRunner(Protocol):
    def run(
        self,
        workspace: CoarseWorkspace,
    ) -> CodexRunResult: ...


@dataclass(frozen=True)
class CoarseStageResult:
    action: str
    output_path: Path
    validation_path: Path
    selected_symbols: tuple[str, ...]
    input_signature: str
    network_status: str
    warnings: tuple[str, ...]
    reused: bool
    output: dict[str, Any]
    validation: CoarseValidationResult
    input_result: CoarseInputBuildResult


def _stage_result(
    *,
    reused: bool,
    output: dict[str, Any],
    validation: CoarseValidationResult,
    input_result: CoarseInputBuildResult,
    output_path: Path,
    validation_path: Path,
) -> CoarseStageResult:
    network = output.get(
        "network_research",
        {},
    )
    network_status = (
        str(network.get("status", "unknown"))
        if isinstance(network, dict)
        else "unknown"
    )
    warning_messages = [
        str(value)
        for value in output.get("warnings", [])
    ]
    warning_messages.extend(
        str(item.get("message", ""))
        for item in validation.warnings
        if item.get("message")
    )
    return CoarseStageResult(
        action="reuse" if reused else "run",
        output_path=output_path,
        validation_path=validation_path,
        selected_symbols=tuple(
            str(item.get("symbol", ""))
            for item in output.get(
                "selections",
                [],
            )
            if isinstance(item, dict)
        ),
        input_signature=(
            input_result.input_signature
        ),
        network_status=network_status,
        warnings=tuple(warning_messages),
        reused=reused,
        output=output,
        validation=validation,
        input_result=input_result,
    )


def _validation_document(
    result: CoarseValidationResult,
    *,
    input_signature: str,
    reused: bool,
    output: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = result.to_dict()
    selections = (
        output.get("selections", [])
        if isinstance(output, dict)
        else []
    )
    symbols = [
        str(item.get("symbol", ""))
        for item in selections
        if isinstance(item, dict)
    ]
    must_include = (
        {
            str(value)
            for value in input_payload.get(
                "must_include",
                [],
            )
        }
        if isinstance(input_payload, dict)
        else set()
    )
    payload.update(
        {
            "input_signature": input_signature,
            "reused": reused,
            "selection_count": len(selections),
            "unique_selection_count": len(
                set(symbols)
            ),
            "must_include_count": len(
                must_include & set(symbols)
            ),
        }
    )
    return payload


def _install_current_revision(
    daily_paths: Any,
    revision: Any,
) -> None:
    atomic_write_json(
        daily_paths.coarse_current,
        {
            "schema_version": "1.0",
            "stage": "coarse_selection",
            "input_signature": (
                revision.input_signature
            ),
            "revision_directory": str(
                revision.revision_directory
            ),
            "input_path": str(revision.input),
            "output_path": str(revision.output),
            "validation_path": str(
                revision.validation
            ),
            "installed_at": utc_now_iso(),
        },
    )


def _previous_valid_identity(
    daily_state: DailyState,
    output_path: Path,
) -> tuple[CoarseStatus, str | None, str | None]:
    if (
        daily_state.coarse_status
        == CoarseStatus.VALID
        and output_path.is_file()
    ):
        return (
            daily_state.coarse_status,
            daily_state.coarse_output_path,
            daily_state.coarse_input_signature,
        )
    return (
        CoarseStatus.MISSING,
        None,
        None,
    )


def _restore_or_fail_daily(
    daily_state: DailyState,
    previous: tuple[
        CoarseStatus,
        str | None,
        str | None,
    ],
) -> None:
    status, output_path, signature = previous
    if status == CoarseStatus.VALID:
        daily_state.coarse_status = status
        daily_state.coarse_output_path = (
            output_path
        )
        daily_state.coarse_input_signature = (
            signature
        )
    else:
        daily_state.coarse_status = (
            CoarseStatus.FAILED
        )
        daily_state.coarse_output_path = None
        daily_state.coarse_input_signature = None
    daily_state.updated_at = utc_now_iso()


def _validate_existing(
    *,
    daily_state: DailyState,
    output_path: Path,
    input_result: CoarseInputBuildResult,
    schema: dict[str, Any],
    force_full: bool,
    now: datetime | None,
) -> tuple[
    dict[str, Any],
    CoarseValidationResult,
] | None:
    if force_full:
        return None
    if not output_path.is_file():
        return None
    try:
        output = load_json_object(output_path)
    except (OSError, ValueError):
        return None
    validation = validate_coarse_output(
        output,
        input_payload=input_result.payload,
        schema=schema,
        now=now,
    )
    if not validation.valid:
        return None
    return output, validation


def execute_coarse_selection(
    *,
    paths: CyclePaths,
    state: CycleState,
    options: CLIOptions,
    config: V2Config,
    runner: CoarseRunner | None = None,
    bar_store: DailyBarStore | None = None,
    now: datetime | None = None,
    release: StrategyRelease | None = None,
) -> CoarseStageResult:
    """Execute Stage C and never touch portfolio or order artifacts."""

    daily_paths = build_daily_paths(
        paths.run_date,
        project_root=paths.project_root,
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=paths.strategy_version,
    )
    active_release = (
        release
        or load_strategy_release(
            paths.strategy_id,
            paths.strategy_version,
            project_root=paths.project_root,
        )
    )
    schema_path = (
        active_release.root
        / "schemas"
        / "coarse_output.schema.json"
    )
    schema = load_coarse_schema(schema_path)
    preflight_output_schema(schema)
    base_snapshot = load_json_object(
        paths.base_snapshot
    )
    guidance = load_initial_guidance(
        paths
    )
    input_result = build_coarse_input(
        config=config,
        run_date=paths.run_date,
        base_snapshot=base_snapshot,
        bar_store=bar_store,
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=(
            paths.strategy_version
        ),
        guidance=guidance.to_dict(),
    )
    revision = build_coarse_revision_paths(
        daily_paths,
        input_result.input_signature,
    )
    revision.revision_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    if not revision.input.exists():
        atomic_write_json(
            revision.input,
            input_result.payload,
        )
    daily_state = load_daily_state(
        paths.daily_state
    )
    previous_output = (
        Path(daily_state.coarse_output_path)
        if daily_state.coarse_output_path
        else revision.output
    )
    previous = _previous_valid_identity(
        daily_state,
        previous_output,
    )
    required = int(
        config.stages["coarse_candidate_count"]
    )
    if len(input_result.candidate_symbols) < required:
        validation = CoarseValidationResult(
            valid=False,
            schema_valid=True,
            business_valid=False,
            errors=(
                {
                    "code": (
                        "COARSE_UNIVERSE_TOO_SMALL"
                    ),
                    "message": (
                        "Python粗筛后的候选不足60只"
                    ),
                    "path": "$.universe",
                },
            ),
            warnings=(),
        )
        atomic_write_json(
            revision.validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
                reused=False,
            ),
        )
        _restore_or_fail_daily(
            daily_state,
            previous,
        )
        save_daily_state(
            paths.daily_state,
            daily_state,
        )
        raise TemporaryDataError(
            "Python粗筛后的候选不足60只，"
            "未调用Codex",
            code="COARSE_UNIVERSE_TOO_SMALL",
            details={
                "candidate_count": len(
                    input_result.candidate_symbols
                ),
                "required_count": required,
            },
        )

    reusable = _validate_existing(
        daily_state=daily_state,
        output_path=revision.output,
        input_result=input_result,
        schema=schema,
        force_full=options.force_full,
        now=now,
    )
    if reusable is not None:
        output, validation = reusable
        atomic_write_json(
            revision.validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
                reused=True,
                output=output,
                input_payload=(
                    input_result.payload
                ),
            ),
        )
        atomic_write_json(
            revision.codex_call,
            {
                "schema_version": "1.0",
                "stage": "coarse_selection",
                "status": "skipped_reused",
                "input_signature": (
                    input_result.input_signature
                ),
                "reused_from_cycle_id": (
                    state.previous_cycle_id
                ),
                "completed_at": utc_now_iso(),
                "attempts": [],
            },
        )
        state.reused_coarse_cycle_id = (
            state.previous_cycle_id
        )
        daily_state.coarse_status = (
            CoarseStatus.VALID
        )
        daily_state.coarse_output_path = str(
            revision.output
        )
        daily_state.coarse_input_signature = (
            input_result.input_signature
        )
        _install_current_revision(
            daily_paths,
            revision,
        )
        save_daily_state(
            paths.daily_state,
            daily_state,
        )
        return _stage_result(
            reused=True,
            output=output,
            validation=validation,
            input_result=input_result,
            output_path=(
                revision.output
            ),
            validation_path=(
                revision.validation
            ),
        )

    daily_state.coarse_status = CoarseStatus.RUNNING
    daily_state.updated_at = utc_now_iso()
    save_daily_state(
        paths.daily_state,
        daily_state,
    )
    workspace = prepare_coarse_workspace(
        revision,
        config=config,
        input_payload=input_result.payload,
        release=active_release,
    )
    active_runner = runner or CodexRunner(
        timeout_seconds=float(
            config.system["codex_timeout_seconds"]
        ),
        retry_count=int(
            config.system["codex_retry_count"]
        ),
        **codex_runner_settings(active_release),
    )
    try:
        run_result = active_runner.run(
            workspace
        )
        atomic_write_json(
            revision.codex_call,
            {
                **run_result.call_record,
                "input_signature": (
                    input_result.input_signature
                ),
            },
        )
        validation = validate_coarse_output(
            run_result.payload,
            input_payload=input_result.payload,
            schema=schema,
            now=now,
        )
        atomic_write_json(
            revision.validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
                reused=False,
                output=run_result.payload,
                input_payload=(
                    input_result.payload
                ),
            ),
        )
        if not validation.valid:
            _restore_or_fail_daily(
                daily_state,
                previous,
            )
            save_daily_state(
                paths.daily_state,
                daily_state,
            )
            raise CodexOutputValidationError(
                "Codex粗选输出未通过Schema或业务校验",
                details={
                    "error_codes": sorted(
                        {
                            str(item["code"])
                            for item
                            in validation.errors
                        }
                    )
                },
            )
        atomic_write_json(
            revision.output,
            run_result.payload,
        )
        daily_state.coarse_status = (
            CoarseStatus.VALID
        )
        daily_state.coarse_output_path = str(
            revision.output
        )
        daily_state.coarse_input_signature = (
            input_result.input_signature
        )
        daily_state.updated_at = utc_now_iso()
        _install_current_revision(
            daily_paths,
            revision,
        )
        save_daily_state(
            paths.daily_state,
            daily_state,
        )
        return _stage_result(
            reused=False,
            output=run_result.payload,
            validation=validation,
            input_result=input_result,
            output_path=(
                revision.output
            ),
            validation_path=(
                revision.validation
            ),
        )
    except Exception as error:
        if not isinstance(
            error,
            CodexOutputValidationError,
        ):
            call_record = getattr(
                active_runner,
                "last_call_record",
                None,
            )
            atomic_write_json(
                revision.codex_call,
                call_record
                if isinstance(call_record, dict)
                else {
                    "schema_version": "1.0",
                    "stage": "coarse_selection",
                    "status": "failed",
                    "input_signature": (
                        input_result.input_signature
                    ),
                    "completed_at": (
                        utc_now_iso()
                    ),
                    "attempts": [],
                    "error_code": getattr(
                        error,
                        "code",
                        "UNEXPECTED_ERROR",
                    ),
                },
            )
            _restore_or_fail_daily(
                daily_state,
                previous,
            )
            save_daily_state(
                paths.daily_state,
                daily_state,
            )
        raise


def run_coarse_stage(
    **kwargs: Any,
) -> CoarseStageResult:
    """Compatibility entrypoint using the explicit Stage C dependencies."""

    return execute_coarse_selection(**kwargs)
