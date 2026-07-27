"""编排 Stage E 执行意图的输入、Codex 调用、校验与原子保存。

作用：读取最新 execution snapshot、组合方案和两轮用户意见，形成第三阶段执行判断。
重要性：校验失败不得安装 output，本阶段不得构建、取消、替换或提交任何实际订单。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from v2.codex.runner import (
    CodexRunResult,
    ExecutionCodexRunner,
)
from v2.codex.validation import (
    preflight_output_schema,
)
from v2.codex.workspace import (
    ExecutionWorkspace,
    prepare_execution_workspace,
)
from v2.config import V2Config
from v2.exceptions import (
    CodexOutputValidationError,
    SafetyBlockedError,
)
from v2.guidance import (
    load_initial_guidance,
)
from v2.models.execution import (
    ExecutionInputBuildResult,
    ExecutionValidationResult,
    build_execution_input,
    validate_execution_output,
)
from v2.models.state import CycleState
from v2.profiles import load_risk_profile
from v2.releases import (
    StrategyRelease,
    load_strategy_release,
)
from v2.review import load_user_review
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


class ExecutionRunner(Protocol):
    def run(
        self,
        workspace: ExecutionWorkspace,
    ) -> CodexRunResult: ...


NON_EXECUTABLE_DECISIONS = frozenset(
    {"defer", "reject", "no_action"}
)


@dataclass(frozen=True)
class ExecutionStageResult:
    action: str
    input_path: Path
    output_path: Path
    validation_path: Path
    input_signature: str
    approve_count: int
    modify_count: int
    defer_count: int
    reject_count: int
    no_action_count: int
    network_status: str
    warnings: tuple[str, ...]
    output: dict[str, Any]
    validation: ExecutionValidationResult
    input_result: ExecutionInputBuildResult


def _neutralize_non_executable_intents(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove residual order intent only from non-executable decisions."""

    result = dict(payload)
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        return result, ()
    decisions: list[object] = []
    normalized_symbols: list[str] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            decisions.append(raw)
            continue
        decision = dict(raw)
        if (
            decision.get("execution_decision")
            not in NON_EXECUTABLE_DECISIONS
        ):
            decisions.append(decision)
            continue
        before = {
            "side": decision.get("side"),
            "execution_fraction": decision.get(
                "execution_fraction"
            ),
            "urgency": decision.get("urgency"),
            "price_condition": decision.get(
                "price_condition"
            ),
            "order_intent": decision.get(
                "order_intent"
            ),
        }
        for key, value in (
            ("side", "none"),
            ("execution_fraction", "0"),
            ("urgency", "none"),
        ):
            if key in decision:
                decision[key] = value
        raw_price = decision.get(
            "price_condition"
        )
        if isinstance(raw_price, dict):
            price = dict(raw_price)
            for key, value in (
                ("reference", "none"),
                ("limit_price", None),
                ("do_not_execute_above", None),
                ("review_below", None),
            ):
                if key in price:
                    price[key] = value
            decision["price_condition"] = price
        raw_intent = decision.get("order_intent")
        if isinstance(raw_intent, dict):
            intent = dict(raw_intent)
            for key, value in (
                ("preferred_type", "none"),
                (
                    "time_in_force_preference",
                    "none",
                ),
                (
                    "extended_hours_requested",
                    False,
                ),
                ("allow_queue", False),
                ("allow_partial_fill", False),
            ):
                if key in intent:
                    intent[key] = value
            decision["order_intent"] = intent
        after = {
            "side": decision.get("side"),
            "execution_fraction": decision.get(
                "execution_fraction"
            ),
            "urgency": decision.get("urgency"),
            "price_condition": decision.get(
                "price_condition"
            ),
            "order_intent": decision.get(
                "order_intent"
            ),
        }
        if after != before:
            normalized_symbols.append(
                str(
                    decision.get(
                        "symbol",
                        "<unknown>",
                    )
                )
            )
        decisions.append(decision)
    result["decisions"] = decisions
    if normalized_symbols:
        raw_warnings = result.get("warnings")
        if isinstance(raw_warnings, list):
            result["warnings"] = [
                *raw_warnings,
                "Python安全归零了非执行决定中的残留订单意图："
                + ",".join(normalized_symbols),
            ]
    return result, tuple(normalized_symbols)


def _capability_paths(
    release: StrategyRelease,
) -> tuple[Path, ...]:
    return (
        release.root
        / "prompts"
        / "execution.md",
        release.root
        / "prompts"
        / "execution_AGENTS.md",
        release.root
        / "schemas"
        / "execution_output.schema.json",
        release.root
        / "config"
        / "execution_policy.json",
    )


def _parse_time(value: object) -> float:
    from datetime import datetime, timezone

    if not isinstance(value, str):
        return 0.0
    normalized = value
    if normalized.endswith("Z"):
        normalized = (
            normalized[:-1] + "+00:00"
        )
    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    return parsed.timestamp()


def _validation_document(
    result: ExecutionValidationResult,
    *,
    input_signature: str,
) -> dict[str, Any]:
    payload = result.to_dict()
    payload.update(
        {
            "input_signature": input_signature,
            "validated_at": utc_now_iso(),
        }
    )
    return payload


def _stage_result(
    *,
    paths: CyclePaths,
    output: dict[str, Any],
    validation: ExecutionValidationResult,
    input_result: ExecutionInputBuildResult,
) -> ExecutionStageResult:
    counts = {
        "approve": 0,
        "modify": 0,
        "defer": 0,
        "reject": 0,
        "no_action": 0,
    }
    for item in output.get("decisions", []):
        if not isinstance(item, dict):
            continue
        decision = str(
            item.get(
                "execution_decision",
                "",
            )
        )
        if decision in counts:
            counts[decision] += 1
    network = output.get(
        "network_research",
        {},
    )
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
    return ExecutionStageResult(
        action="run",
        input_path=paths.execution_input,
        output_path=paths.execution_output,
        validation_path=(
            paths.execution_validation
        ),
        input_signature=(
            input_result.input_signature
        ),
        approve_count=counts["approve"],
        modify_count=counts["modify"],
        defer_count=counts["defer"],
        reject_count=counts["reject"],
        no_action_count=counts["no_action"],
        network_status=str(
            network.get("status", "unknown")
        ),
        warnings=tuple(warnings),
        output=output,
        validation=validation,
        input_result=input_result,
    )


def execute_execution_decision(
    *,
    paths: CyclePaths,
    state: CycleState,
    config: V2Config,
    runner: ExecutionRunner | None = None,
    release: StrategyRelease | None = None,
) -> ExecutionStageResult:
    """Run Stage E and stop before any order construction."""

    if paths.profile_id != "paper1":
        raise SafetyBlockedError(
            "Stage E当前只部署到paper1",
            code="EXECUTION_PROFILE_NOT_PAPER1",
        )
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
        for path in _capability_paths(
            active_release
        )
        if not path.is_file()
    ]
    if missing:
        raise SafetyBlockedError(
            "strategy release不包含execution能力",
            code="EXECUTION_CAPABILITY_MISSING",
            details={"missing": missing},
        )
    schema = load_json_object(
        active_release.root
        / "schemas"
        / "execution_output.schema.json"
    )
    preflight_output_schema(schema)
    policy = load_json_object(
        active_release.root
        / "config"
        / "execution_policy.json"
    )
    required_cycle_files = (
        paths.initial_guidance,
        paths.user_review,
        paths.portfolio_output,
        paths.execution_snapshot,
    )
    missing_cycle = [
        str(path)
        for path in required_cycle_files
        if not path.is_file()
    ]
    if missing_cycle:
        raise SafetyBlockedError(
            "Stage E缺少当前cycle必要输入",
            code="EXECUTION_INPUT_MISSING",
            details={"missing": missing_cycle},
        )
    guidance = load_initial_guidance(
        paths
    )
    review = load_user_review(paths)
    portfolio = load_json_object(
        paths.portfolio_output
    )
    snapshot = load_json_object(
        paths.execution_snapshot
    )
    if (
        snapshot.get("profile_id")
        != paths.profile_id
        or snapshot.get("strategy_id")
        != paths.strategy_id
        or snapshot.get("strategy_version")
        != paths.strategy_version
        or snapshot.get("run_date")
        != paths.run_date
        or snapshot.get("cycle_id")
        != paths.cycle_id
    ):
        raise SafetyBlockedError(
            "execution snapshot身份不匹配",
            code="EXECUTION_SNAPSHOT_IDENTITY_MISMATCH",
        )
    if _parse_time(
        snapshot.get("retrieved_at")
    ) <= _parse_time(
        portfolio.get("generated_at")
    ):
        raise SafetyBlockedError(
            "execution snapshot必须晚于portfolio output",
            code="EXECUTION_SNAPSHOT_NOT_FRESHER",
        )
    data_quality = snapshot.get(
        "data_quality",
        {},
    )
    if (
        not isinstance(data_quality, dict)
        or data_quality.get(
            "execution_ready"
        )
        is not True
    ):
        raise SafetyBlockedError(
            "执行级账户或订单数据不完整",
            code="EXECUTION_SNAPSHOT_NOT_READY",
        )
    account = snapshot.get("account")
    if (
        not isinstance(account, dict)
        or account.get("trading_blocked")
        is True
        or account.get("account_blocked")
        is True
        or account.get(
            "trade_suspended_by_user"
        )
        is True
    ):
        raise SafetyBlockedError(
            "账户已阻止交易，不能运行执行代理",
            code="EXECUTION_ACCOUNT_BLOCKED",
        )
    risk_profile = load_risk_profile(
        state.release["risk_profile"],
        project_root=paths.project_root,
    )
    input_result = build_execution_input(
        paths=paths,
        state=state,
        initial_guidance=guidance.to_dict(),
        user_review=review.to_dict(),
        portfolio_output=portfolio,
        execution_snapshot=snapshot,
        risk_profile=risk_profile,
        risk_limits=config.risk,
        execution_policy=policy,
        release=active_release,
    )
    atomic_write_json(
        paths.execution_input,
        input_result.payload,
    )
    workspace = prepare_execution_workspace(
        paths,
        input_payload=input_result.payload,
        release=active_release,
    )
    active_runner = (
        runner
        or ExecutionCodexRunner(
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
        )
    )
    try:
        run_result = active_runner.run(
            workspace
        )
        normalized_output, neutralized = (
            _neutralize_non_executable_intents(
                run_result.payload
            )
        )
        atomic_write_json(
            paths.execution_codex_call,
            {
                **run_result.call_record,
                "input_signature": (
                    input_result.input_signature
                ),
                "safe_normalizations": [
                    {
                        "type": (
                            "neutralize_non_executable_intent"
                        ),
                        "symbol": symbol,
                    }
                    for symbol in neutralized
                ],
            },
        )
        validation = validate_execution_output(
            normalized_output,
            input_payload=input_result.payload,
            schema=schema,
        )
        atomic_write_json(
            paths.execution_validation,
            _validation_document(
                validation,
                input_signature=(
                    input_result.input_signature
                ),
            ),
        )
        if not validation.valid:
            raise CodexOutputValidationError(
                "Codex execution输出未通过Schema或业务校验",
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
            paths.execution_output,
            normalized_output,
        )
        return _stage_result(
            paths=paths,
            output=normalized_output,
            validation=validation,
            input_result=input_result,
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
                paths.execution_codex_call,
                record
                if isinstance(record, dict)
                else {
                    "schema_version": "1.0",
                    "stage": "execution_decision",
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


def run_execution_stage(
    **kwargs: Any,
) -> ExecutionStageResult:
    return execute_execution_decision(**kwargs)
