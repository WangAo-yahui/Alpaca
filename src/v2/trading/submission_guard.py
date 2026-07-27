"""集中执行 Stage G 每次券商写操作之前的最终安全门禁。

作用：核对 CLI、profile环境、账户绑定、部署开关、release、产物哈希、状态机和 journal。
重要性：任何一项不一致都会 fail closed；业务模块不能绕过这里把本地意图升级为券商写操作。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from v2.data.alpaca_client import AlpacaClients
from v2.exceptions import SafetyBlockedError
from v2.models.orders import canonical_hash
from v2.models.state import CycleState, StepName
from v2.profiles import Profile, SubmissionPolicy
from v2.releases import sha256_file
from v2.trading.submission_journal import SubmissionJournal


def assert_submission_allowed(
    *,
    profile: Profile,
    policy: SubmissionPolicy,
    state: CycleState,
    clients: AlpacaClients,
    account: Mapping[str, Any],
    expected_account_hash: str | None,
    validated: Mapping[str, Any],
    request_specs: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    intent: Mapping[str, Any],
    journal: SubmissionJournal,
    broker_submission_exists: bool,
) -> None:
    """Raise one secret-free safety block unless every write gate is true."""

    switches = policy.settings.get("deployment_switches", {})
    policy_hash = sha256_file(policy.source_path)
    expected_paper = profile.environment == "paper"
    environment_switch_enabled = (
        switches.get(
            f"{profile.environment}_submission_enabled"
        )
        is True
        if isinstance(switches, Mapping)
        else False
    )
    opposite_switch_disabled = (
        switches.get(
            (
                "live_submission_enabled"
                if expected_paper
                else "paper_submission_enabled"
            )
        )
        is False
        if isinstance(switches, Mapping)
        else False
    )
    contains_protective_order = any(
        isinstance(item, Mapping)
        and str(
            item.get("protection_role", "none")
        )
        != "none"
        for item in request_specs.get(
            "requests",
            [],
        )
    )
    checks = {
        "allow_trade": state.invocation.allow_trade,
        "supported_environment": (
            profile.environment in {"paper", "live"}
        ),
        "policy_environment": (
            policy.environment == profile.environment
        ),
        "profile_enabled": profile.enabled,
        "client_environment": (
            clients.paper == expected_paper
        ),
        "invocation_environment": (
            bool(
                getattr(
                    state.invocation,
                    "paper",
                    not bool(
                        getattr(
                            state.invocation,
                            "live",
                            False,
                        )
                    ),
                )
            )
            == expected_paper
            and bool(
                getattr(
                    state.invocation,
                    "live",
                    False,
                )
            )
            == (not expected_paper)
        ),
        "submit_step": (
            state.current_step == StepName.SUBMIT_ORDERS
        ),
        "validated_identity": (
            validated.get("profile_id") == profile.profile_id
            and validated.get("cycle_id") == state.cycle_id
            and intent.get(
                "profile_id", profile.profile_id
            )
            == profile.profile_id
            and intent.get(
                "environment", profile.environment
            )
            == profile.environment
        ),
        "submission_requested": (
            validated.get("submission_requested") is True
            and request_specs.get("submission_requested") is True
            and action_plan.get("submission_requested") is True
        ),
        "not_performed": (
            validated.get("submission_performed") is False
            and request_specs.get("submission_performed") is False
            and action_plan.get("submission_performed") is False
            and not broker_submission_exists
        ),
        "validated_hash": (
            intent.get("validated_orders_hash")
            == canonical_hash(validated)
        ),
        "request_specs_hash": (
            intent.get("request_specs_hash")
            == canonical_hash(request_specs)
        ),
        "action_plan_hash": (
            intent.get("action_plan_hash")
            == canonical_hash(action_plan)
        ),
        "policy_reference": (
            intent.get("submission_policy") == policy.reference
            and state.release.get("submission_policy")
            == policy.reference
        ),
        "policy_hash": (
            intent.get("submission_policy_hash") == policy_hash
            and state.release.get("submission_policy_hash")
            == policy_hash
        ),
        "policy_allows": (
            policy.settings.get("allow_submit") is True
            and policy.settings.get("allow_direct_replace") is False
            and policy.settings.get("submit_orders_sequentially") is True
        ),
        "no_uncertain": not journal.has_uncertain,
        "credential_identity": (
            bool(profile.credential_key_env)
            and bool(profile.credential_secret_env)
        ),
        "account_hash": (
            expected_account_hash is not None
            and account.get("account_id_hash")
            == expected_account_hash
        ),
        "account_tradable": (
            str(account.get("status", "")).upper() == "ACTIVE"
            and not bool(account.get("trading_blocked"))
            and not bool(account.get("account_blocked"))
            and not bool(account.get("trade_suspended_by_user"))
        ),
        "environment_switch": environment_switch_enabled,
        "protective_order_switch": (
            not contains_protective_order
            or (
                isinstance(switches, Mapping)
                and switches.get(
                    "protective_order_submission_enabled"
                )
                is True
            )
        ),
        "opposite_environment_switch_off": (
            opposite_switch_disabled
        ),
        "kill_switch_off": (
            isinstance(switches, Mapping)
            and switches.get("global_kill_switch") is False
            and switches.get("emergency_stop") is False
        ),
    }
    failed = sorted(
        name for name, passed in checks.items() if not passed
    )
    if failed:
        raise SafetyBlockedError(
            "Stage G最终写前安全检查失败",
            code="SUBMISSION_PREFLIGHT_BLOCKED",
            details={"failed_checks": failed},
        )
