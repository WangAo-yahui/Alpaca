"""编排 Stage D 组合决策的输入、复用、Codex 调用和原子安装。

作用：从当前有效 coarse revision 构建组合输入，选择 run/reuse，验证后写入本轮目录。
重要性：旧有效方案在失败时必须保持不变，且本阶段禁止触碰执行和订单 API。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from v2.cli import CLIOptions
from v2.codex.runner import (
    CodexRunResult,
    PortfolioCodexRunner,
    codex_runner_settings,
)
from v2.codex.validation import (
    load_coarse_schema,
    preflight_output_schema,
    validate_coarse_output,
)
from v2.codex.workspace import (
    PortfolioWorkspace,
    prepare_portfolio_workspace,
)
from v2.config import V2Config
from v2.exceptions import (
    CodexOutputValidationError,
    ConfigurationError,
    SafetyBlockedError,
)
from v2.guidance import load_initial_guidance
from v2.models.portfolio import (
    PortfolioInputBuildResult,
    PortfolioReuseDecision,
    PortfolioValidationResult,
    build_portfolio_input,
    should_run_portfolio,
    validate_portfolio_output,
)
from v2.models.state import (
    CoarseStatus,
    CycleState,
    DailyState,
    load_daily_state,
    save_daily_state,
)
from v2.profiles import load_risk_profile
from v2.releases import (
    StrategyRelease,
    load_strategy_release,
)
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


class PortfolioRunner(Protocol):
    def run(
        self,
        workspace: PortfolioWorkspace,
    ) -> CodexRunResult: ...


def _normalize_model_timing(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any],
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Make application-owned portfolio timing deterministic after validation."""

    generated = now or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    generated = generated.astimezone(timezone.utc)
    valid_minutes = int(
        policy.get("portfolio_valid_minutes", 0)
    )
    if valid_minutes <= 0:
        raise ConfigurationError(
            "portfolio有效期配置必须大于0",
            code="PORTFOLIO_VALIDITY_POLICY_INVALID",
        )
    normalized = dict(payload)
    original = {
        "generated_at": str(
            normalized.get("generated_at", "")
        ),
        "valid_until": str(
            normalized.get("valid_until", "")
        ),
    }
    normalized["generated_at"] = generated.isoformat()
    normalized["valid_until"] = (
        generated + timedelta(minutes=valid_minutes)
    ).isoformat()
    return normalized, original


def _model_generated_at(
    payload: dict[str, Any],
) -> datetime | None:
    value = payload.get("generated_at")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        generated = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated.astimezone(timezone.utc)


@dataclass(frozen=True)
class PortfolioStageResult:
    action: str
    source_cycle_id: str | None
    input_path: Path
    output_path: Path
    validation_path: Path
    input_signature: str
    target_cash_weight: Decimal
    target_symbol_count: int
    network_status: str
    warnings: tuple[str, ...]
    output: dict[str, Any]
    validation: PortfolioValidationResult
    input_result: PortfolioInputBuildResult

    @property
    def reused(self) -> bool:
        return self.action == "reuse"


def _load_policy(
    release: StrategyRelease,
) -> dict[str, Any]:
    path = (
        release.root
        / "config"
        / "portfolio_policy.json"
    )
    if not path.is_file():
        raise ConfigurationError(
            "strategy release不包含portfolio policy",
            code="PORTFOLIO_CAPABILITY_MISSING",
        )
    return load_json_object(path)


def _portfolio_capability_paths(
    release: StrategyRelease,
) -> tuple[Path, ...]:
    return (
        release.root / "prompts" / "portfolio.md",
        release.root
        / "prompts"
        / "portfolio_AGENTS.md",
        release.root
        / "schemas"
        / "portfolio_output.schema.json",
        release.root
        / "config"
        / "portfolio_policy.json",
    )


def _portfolio_output_from_state(
    *,
    state: DailyState,
    cycles_directory: Path,
) -> dict[str, Any] | None:
    raw_path = state.latest_valid_portfolio_output_path
    if not raw_path:
        return None
    output_path = Path(raw_path)
    if not output_path.is_file():
        return None
    try:
        if not output_path.resolve().is_relative_to(
            cycles_directory.resolve()
        ):
            return None
        return load_json_object(output_path)
    except (OSError, ValueError):
        return None


def _previous_portfolio_context(
    *,
    paths: CyclePaths,
    daily_state: DailyState,
) -> dict[str, Any]:
    """Load today's latest plan, or the newest valid prior-day plan."""

    current = _portfolio_output_from_state(
        state=daily_state,
        cycles_directory=paths.cycles_directory,
    )
    if current is not None:
        return current
    try:
        strategy_root = paths.identity_root.parent
        day_directories = [
            day_directory
            for version_directory
            in strategy_root.iterdir()
            if version_directory.is_dir()
            for day_directory
            in version_directory.iterdir()
            if day_directory.is_dir()
            and day_directory.name < paths.run_date
        ]
    except OSError:
        return {}
    prior_states: list[
        tuple[str, Path, DailyState]
    ] = []
    for day_directory in day_directories:
        state_path = day_directory / "daily_state.json"
        if not state_path.is_file():
            continue
        try:
            previous_state = load_daily_state(state_path)
        except (OSError, ValueError):
            continue
        if (
            previous_state.profile_id != paths.profile_id
            or previous_state.strategy_id
            != paths.strategy_id
        ):
            continue
        prior_states.append(
            (
                previous_state.latest_cycle_id or "",
                day_directory,
                previous_state,
            )
        )
    for _, day_directory, previous_state in sorted(
        prior_states,
        key=lambda item: (
            item[1].name,
            item[0],
            item[2].strategy_version,
        ),
        reverse=True,
    ):
        previous = _portfolio_output_from_state(
            state=previous_state,
            cycles_directory=(
                day_directory / "cycles"
            ),
        )
        if previous is not None:
            return previous
    return {}


def _validation_document(
    validation: PortfolioValidationResult,
    *,
    input_signature: str,
    action: str,
    source_cycle_id: str | None,
) -> dict[str, Any]:
    payload = validation.to_dict()
    payload.update(
        {
            "input_signature": input_signature,
            "action": action,
            "source_cycle_id": source_cycle_id,
            "validated_at": utc_now_iso(),
        }
    )
    return payload


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _material_capital_change(
    current_input: dict[str, Any],
    source_input: dict[str, Any],
    policy: dict[str, Any],
) -> bool:
    current_components = current_input.get(
        "input_components",
        {},
    )
    source_components = source_input.get(
        "input_components",
        {},
    )
    if not isinstance(
        current_components,
        dict,
    ) or not isinstance(
        source_components,
        dict,
    ):
        return True
    current_capital = _decimal(
        current_components.get(
            "allocatable_capital"
        )
    )
    source_capital = _decimal(
        source_components.get(
            "allocatable_capital"
        )
    )
    materiality = policy.get(
        "capital_change_materiality",
        {},
    )
    materiality = (
        materiality
        if isinstance(materiality, dict)
        else {}
    )
    absolute_limit = _decimal(
        materiality.get("absolute_usd", "0")
    )
    relative_limit = _decimal(
        materiality.get(
            "relative_fraction",
            "0",
        )
    )
    difference = abs(
        current_capital - source_capital
    )
    relative = (
        difference / abs(source_capital)
        if source_capital != 0
        else (
            Decimal("0")
            if difference == 0
            else Decimal("Infinity")
        )
    )
    return (
        difference >= absolute_limit
        or relative >= relative_limit
    )


def _same_except_immaterial_capital(
    current_input: dict[str, Any],
    source_input: dict[str, Any],
    policy: dict[str, Any],
) -> bool:
    current = current_input.get(
        "input_components"
    )
    source = source_input.get(
        "input_components"
    )
    if not isinstance(current, dict) or not isinstance(
        source,
        dict,
    ):
        return False
    current_stable = dict(current)
    source_stable = dict(source)
    current_stable.pop("allocatable_capital", None)
    source_stable.pop("allocatable_capital", None)
    return (
        current_stable == source_stable
        and not _material_capital_change(
            current_input,
            source_input,
            policy,
        )
    )


def _source_portfolio(
    *,
    paths: CyclePaths,
    input_result: PortfolioInputBuildResult,
    schema: dict[str, Any],
    policy: dict[str, Any],
    force_rebalance: bool,
    now: datetime | None,
) -> tuple[
    PortfolioReuseDecision,
    dict[str, Any] | None,
]:
    daily_state = load_daily_state(
        paths.daily_state
    )
    source_cycle_id = (
        daily_state.latest_valid_portfolio_cycle_id
    )
    source_output_path = (
        Path(
            daily_state.latest_valid_portfolio_output_path
        )
        if daily_state.latest_valid_portfolio_output_path
        else None
    )
    source_input_path = (
        source_output_path.parent / "input.json"
        if source_output_path is not None
        else None
    )
    source_validation_path = (
        source_output_path.parent
        / "validation.json"
        if source_output_path is not None
        else None
    )
    source_output: dict[str, Any] | None = None
    source_valid = False
    signatures_equivalent = False
    source_valid_until = (
        daily_state.latest_portfolio_valid_until
    )
    if (
        source_output_path is not None
        and source_input_path is not None
        and source_validation_path is not None
        and source_output_path.is_file()
        and source_input_path.is_file()
        and source_validation_path.is_file()
        and source_output_path.resolve().is_relative_to(
            paths.cycles_directory.resolve()
        )
    ):
        try:
            source_output = load_json_object(
                source_output_path
            )
            source_input = load_json_object(
                source_input_path
            )
            source_validation_document = (
                load_json_object(
                    source_validation_path
                )
            )
            source_validation = (
                validate_portfolio_output(
                    source_output,
                    input_payload=source_input,
                    schema=schema,
                    now=now,
                )
            )
            source_valid = (
                source_validation.valid
                and source_validation_document.get(
                    "valid"
                )
                is True
            )
            signatures_equivalent = (
                input_result.input_signature
                == daily_state
                .latest_portfolio_input_signature
                or _same_except_immaterial_capital(
                    input_result.payload,
                    source_input,
                    policy,
                )
            )
        except (OSError, ValueError):
            source_valid = False
    decision = should_run_portfolio(
        {
            "force_rebalance": force_rebalance,
            "source_cycle_id": source_cycle_id,
            "input_signature": (
                input_result.input_signature
                if signatures_equivalent
                else "current-changed"
            ),
            "source_input_signature": (
                input_result.input_signature
                if signatures_equivalent
                else daily_state
                .latest_portfolio_input_signature
            ),
            "source_valid": source_valid,
            "source_valid_until": (
                source_valid_until
            ),
            "now": (
                now.isoformat()
                if now is not None
                else utc_now_iso()
            ),
        }
    )
    return decision, source_output


def _result(
    *,
    action: str,
    source_cycle_id: str | None,
    paths: CyclePaths,
    input_result: PortfolioInputBuildResult,
    output: dict[str, Any],
    validation: PortfolioValidationResult,
) -> PortfolioStageResult:
    allocation = output.get("allocation")
    allocation = (
        allocation
        if isinstance(allocation, dict)
        else {}
    )
    network = output.get("network_research")
    network = (
        network
        if isinstance(network, dict)
        else {}
    )
    warnings = [
        str(value)
        for value in output.get("warnings", [])
    ]
    warnings.extend(
        str(item.get("message", ""))
        for item in validation.warnings
        if item.get("message")
    )
    return PortfolioStageResult(
        action=action,
        source_cycle_id=source_cycle_id,
        input_path=paths.portfolio_input,
        output_path=paths.portfolio_output,
        validation_path=(
            paths.portfolio_validation
        ),
        input_signature=(
            input_result.input_signature
        ),
        target_cash_weight=_decimal(
            allocation.get("target_cash_weight")
        ),
        target_symbol_count=int(
            allocation.get(
                "target_position_count",
                0,
            )
        ),
        network_status=str(
            network.get("status", "unknown")
        ),
        warnings=tuple(warnings),
        output=output,
        validation=validation,
        input_result=input_result,
    )


def execute_portfolio_decision(
    *,
    paths: CyclePaths,
    state: CycleState,
    options: CLIOptions,
    config: V2Config,
    runner: PortfolioRunner | None = None,
    now: datetime | None = None,
    release: StrategyRelease | None = None,
) -> PortfolioStageResult:
    """Run or reuse Stage D and never call broker execution/order methods."""

    active_release = (
        release
        or load_strategy_release(
            paths.strategy_id,
            paths.strategy_version,
            project_root=paths.project_root,
        )
    )
    missing = [
        str(path)
        for path in _portfolio_capability_paths(
            active_release
        )
        if not path.is_file()
    ]
    if missing:
        raise SafetyBlockedError(
            "当前strategy release不包含portfolio能力",
            code="PORTFOLIO_CAPABILITY_MISSING",
            details={"missing": missing},
        )
    schema = load_json_object(
        active_release.root
        / "schemas"
        / "portfolio_output.schema.json"
    )
    preflight_output_schema(schema)
    policy = _load_policy(active_release)
    base_snapshot = load_json_object(
        paths.base_snapshot
    )
    data_quality = base_snapshot.get(
        "data_quality",
        {},
    )
    if (
        not isinstance(data_quality, dict)
        or data_quality.get("decision_ready")
        is not True
    ):
        raise SafetyBlockedError(
            "基础快照关键数据不完整，阻止组合决策",
            code="PORTFOLIO_BASE_DATA_BLOCKED",
        )
    account = base_snapshot.get("account")
    if (
        not isinstance(account, dict)
        or account.get("trading_blocked") is True
        or account.get("account_blocked") is True
        or account.get(
            "trade_suspended_by_user"
        )
        is True
    ):
        raise SafetyBlockedError(
            "账户已阻止交易，不能形成组合方案",
            code="PORTFOLIO_ACCOUNT_BLOCKED",
        )
    daily_state = load_daily_state(
        paths.daily_state
    )
    if (
        daily_state.coarse_status
        != CoarseStatus.VALID
        or daily_state.coarse_output_path is None
        or not Path(
            daily_state.coarse_output_path
        ).is_file()
    ):
        raise SafetyBlockedError(
            "没有当前有效coarse输出",
            code="PORTFOLIO_COARSE_MISSING",
        )
    coarse_output_path = Path(
        daily_state.coarse_output_path
    )
    if not coarse_output_path.resolve().is_relative_to(
        paths.coarse_revisions.resolve()
    ):
        raise SafetyBlockedError(
            "coarse输出路径不属于当前身份与日期",
            code="PORTFOLIO_COARSE_IDENTITY_MISMATCH",
        )
    coarse_input_path = (
        coarse_output_path.parent / "input.json"
    )
    if not coarse_input_path.is_file():
        raise SafetyBlockedError(
            "当前coarse revision缺少输入",
            code="PORTFOLIO_COARSE_INPUT_MISSING",
        )
    coarse_output = load_json_object(
        coarse_output_path
    )
    coarse_input = load_json_object(
        coarse_input_path
    )
    coarse_schema = load_coarse_schema(
        active_release.root
        / "schemas"
        / "coarse_output.schema.json"
    )
    preflight_output_schema(coarse_schema)
    coarse_validation = validate_coarse_output(
        coarse_output,
        input_payload=coarse_input,
        schema=coarse_schema,
        now=now,
    )
    if (
        not coarse_validation.valid
        or coarse_output.get("input_signature")
        != daily_state.coarse_input_signature
    ):
        raise SafetyBlockedError(
            "当前coarse revision未通过重新校验",
            code="PORTFOLIO_COARSE_INVALID",
        )
    guidance = load_initial_guidance(paths)
    risk_profile = load_risk_profile(
        state.release["risk_profile"],
        project_root=paths.project_root,
    )
    previous_output = _previous_portfolio_context(
        paths=paths,
        daily_state=daily_state,
    )
    input_result = build_portfolio_input(
        paths=paths,
        base_snapshot=base_snapshot,
        coarse_output=coarse_output,
        coarse_input=coarse_input,
        initial_guidance=guidance.to_dict(),
        policy=policy,
        risk_profile=risk_profile,
        release=active_release,
        trigger={
            "cycle_kind": state.cycle_kind.value,
            "force_rebalance": (
                options.force_rebalance
            ),
            "force_full": options.force_full,
        },
        previous_portfolio=previous_output,
    )
    atomic_write_json(
        paths.portfolio_input,
        input_result.payload,
    )
    reuse_decision, source_output = (
        _source_portfolio(
            paths=paths,
            input_result=input_result,
            schema=schema,
            policy=policy,
            force_rebalance=(
                options.force_rebalance
            ),
            now=now,
        )
    )
    if (
        reuse_decision.action == "reuse"
        and source_output is not None
    ):
        output = dict(source_output)
        output["cycle_id"] = paths.cycle_id
        output["input_signature"] = (
            input_result.input_signature
        )
        validation = validate_portfolio_output(
            output,
            input_payload=input_result.payload,
            schema=schema,
            now=now,
        )
        if validation.valid:
            atomic_write_json(
                paths.portfolio_output,
                output,
            )
            atomic_write_json(
                paths.portfolio_validation,
                _validation_document(
                    validation,
                    input_signature=(
                        input_result.input_signature
                    ),
                    action="reuse",
                    source_cycle_id=(
                        reuse_decision.source_cycle_id
                    ),
                ),
            )
            atomic_write_json(
                paths.portfolio_reuse,
                {
                    "schema_version": "1.0",
                    "reused": True,
                    "source_cycle_id": (
                        reuse_decision.source_cycle_id
                    ),
                    "source_output_path": (
                        daily_state
                        .latest_valid_portfolio_output_path
                    ),
                    "source_input_signature": (
                        daily_state
                        .latest_portfolio_input_signature
                    ),
                    "reused_at": utc_now_iso(),
                    "reasons": list(
                        reuse_decision.reasons
                    ),
                },
            )
            atomic_write_json(
                paths.portfolio_codex_call,
                {
                    "schema_version": "1.0",
                    "stage": "portfolio_decision",
                    "status": "skipped_reused",
                    "input_signature": (
                        input_result.input_signature
                    ),
                    "reused_from_cycle_id": (
                        reuse_decision.source_cycle_id
                    ),
                    "completed_at": utc_now_iso(),
                    "attempts": [],
                },
            )
            state.reused_portfolio_cycle_id = (
                reuse_decision.source_cycle_id
            )
            daily_state.latest_valid_portfolio_cycle_id = (
                paths.cycle_id
            )
            daily_state.latest_valid_portfolio_output_path = str(
                paths.portfolio_output
            )
            daily_state.latest_portfolio_input_signature = (
                input_result.input_signature
            )
            daily_state.latest_portfolio_valid_until = str(
                output.get("valid_until", "")
            )
            save_daily_state(
                paths.daily_state,
                daily_state,
            )
            return _result(
                action="reuse",
                source_cycle_id=(
                    reuse_decision.source_cycle_id
                ),
                paths=paths,
                input_result=input_result,
                output=output,
                validation=validation,
            )

    workspace = prepare_portfolio_workspace(
        paths,
        input_payload=input_result.payload,
        release=active_release,
        risk_profile_payload=load_json_object(
            risk_profile.source_path
        ),
    )
    active_runner = (
        runner
        or PortfolioCodexRunner(
            timeout_seconds=float(
                config.system[
                    "codex_timeout_seconds"
                ]
            ),
            retry_count=int(
                config.system[
                    "codex_retry_count"
                ]
            ),
            **codex_runner_settings(active_release),
        )
    )
    try:
        run_result = active_runner.run(
            workspace
        )
        raw_validation = validate_portfolio_output(
            run_result.payload,
            input_payload=input_result.payload,
            schema=schema,
            now=_model_generated_at(
                dict(run_result.payload)
            ),
        )
        if not raw_validation.valid:
            atomic_write_json(
                paths.portfolio_codex_call,
                {
                    **run_result.call_record,
                    "input_signature": (
                        input_result.input_signature
                    ),
                },
            )
            atomic_write_json(
                paths.portfolio_validation,
                _validation_document(
                    raw_validation,
                    input_signature=(
                        input_result.input_signature
                    ),
                    action="run",
                    source_cycle_id=None,
                ),
            )
            raise CodexOutputValidationError(
                "Codex组合输出未通过Schema或业务校验",
                details={
                    "error_codes": sorted(
                        {
                            str(item["code"])
                            for item
                            in raw_validation.errors
                        }
                    )
                },
            )
        normalized_output, original_timing = (
            _normalize_model_timing(
                dict(run_result.payload),
                policy=dict(policy),
                now=now,
            )
        )
        atomic_write_json(
            paths.portfolio_codex_call,
            {
                **run_result.call_record,
                "input_signature": (
                    input_result.input_signature
                ),
                "safe_normalizations": [
                    {
                        "type": (
                            "application_owned_portfolio_timing"
                        ),
                        "original": original_timing,
                        "normalized": {
                            "generated_at": normalized_output[
                                "generated_at"
                            ],
                            "valid_until": normalized_output[
                                "valid_until"
                            ],
                        },
                    }
                ],
            },
        )
        validation = validate_portfolio_output(
            normalized_output,
            input_payload=input_result.payload,
            schema=schema,
            now=now,
        )
        atomic_write_json(
            paths.portfolio_validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
                action="run",
                source_cycle_id=None,
            ),
        )
        if not validation.valid:
            raise CodexOutputValidationError(
                "Codex组合输出未通过Schema或业务校验",
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
            paths.portfolio_output,
            normalized_output,
        )
        daily_state.latest_valid_portfolio_cycle_id = (
            paths.cycle_id
        )
        daily_state.latest_valid_portfolio_output_path = str(
            paths.portfolio_output
        )
        daily_state.latest_portfolio_input_signature = (
            input_result.input_signature
        )
        daily_state.latest_portfolio_valid_until = str(
            normalized_output.get(
                "valid_until",
                "",
            )
        )
        save_daily_state(
            paths.daily_state,
            daily_state,
        )
        return _result(
            action="run",
            source_cycle_id=None,
            paths=paths,
            input_result=input_result,
            output=normalized_output,
            validation=validation,
        )
    except Exception as error:
        if not isinstance(
            error,
            CodexOutputValidationError,
        ):
            record = getattr(
                active_runner,
                "last_call_record",
                None,
            )
            atomic_write_json(
                paths.portfolio_codex_call,
                record
                if isinstance(record, dict)
                else {
                    "schema_version": "1.0",
                    "stage": "portfolio_decision",
                    "status": "failed",
                    "input_signature": (
                        input_result.input_signature
                    ),
                    "completed_at": utc_now_iso(),
                    "attempts": [],
                    "error_code": getattr(
                        error,
                        "code",
                        "UNEXPECTED_ERROR",
                    ),
                },
            )
        raise


def run_portfolio_stage(
    **kwargs: Any,
) -> PortfolioStageResult:
    return execute_portfolio_decision(**kwargs)
