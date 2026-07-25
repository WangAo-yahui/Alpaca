"""装载账户 profile、版本化风险配置并执行显式账户绑定。

作用：解析非敏感的凭据环境变量名，使用 SHA-256 绑定 Alpaca account id。
重要性：它防止 profile 串用账户或风险规则；原始账户号和密钥绝不能写入运行产物。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from v2.exceptions import ConfigurationError
from v2.runtime import (
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


PROFILE_COMPONENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
)
RISK_REFERENCE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)@"
    r"([A-Za-z0-9][A-Za-z0-9._-]*)$"
)


@dataclass(frozen=True)
class Profile:
    schema_version: str
    profile_id: str
    enabled: bool
    broker: str
    environment: str
    credential_key_env: str
    credential_secret_env: str
    strategy_id: str
    strategy_version: str
    risk_profile: str
    source_path: Path

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ConfigurationError(
                "profile schema_version不支持",
                code="PROFILE_SCHEMA_UNSUPPORTED",
            )
        for label, value in (
            ("profile_id", self.profile_id),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
        ):
            if not PROFILE_COMPONENT.fullmatch(value):
                raise ConfigurationError(
                    f"profile {label}格式无效",
                    code="PROFILE_INVALID",
                    details={"field": label},
                )
        if self.broker != "alpaca":
            raise ConfigurationError(
                "v2 profile只支持alpaca",
                code="PROFILE_BROKER_UNSUPPORTED",
            )
        if self.environment not in {
            "paper",
            "live",
        }:
            raise ConfigurationError(
                "profile environment必须为paper或live",
                code="PROFILE_INVALID",
            )
        for field, value in (
            (
                "credential_key_env",
                self.credential_key_env,
            ),
            (
                "credential_secret_env",
                self.credential_secret_env,
            ),
        ):
            if not re.fullmatch(
                r"[A-Z_][A-Z0-9_]*",
                value,
            ):
                raise ConfigurationError(
                    "profile凭据字段必须是环境变量名称",
                    code="PROFILE_CREDENTIAL_ENV_INVALID",
                    details={"field": field},
                )
        if not RISK_REFERENCE.fullmatch(
            self.risk_profile
        ):
            raise ConfigurationError(
                "risk_profile必须使用name@version格式",
                code="RISK_PROFILE_REFERENCE_INVALID",
            )


@dataclass(frozen=True)
class RiskProfile:
    schema_version: str
    risk_profile_id: str
    risk_profile_version: str
    environment: str
    settings: Mapping[str, Any]
    source_path: Path

    @property
    def reference(self) -> str:
        return (
            f"{self.risk_profile_id}@"
            f"{self.risk_profile_version}"
        )


def _project_root(
    project_root: Path | None,
) -> Path:
    return (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    *,
    code: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            f"配置字段不能为空：{key}",
            code=code,
            details={"field": key},
        )
    return value.strip()


def load_profile(
    profile_id: str,
    *,
    project_root: Path | None = None,
    require_enabled: bool = True,
) -> Profile:
    normalized = str(profile_id).strip()
    if not PROFILE_COMPONENT.fullmatch(normalized):
        raise ConfigurationError(
            "profile名称格式无效",
            code="PROFILE_INVALID",
        )
    root = _project_root(project_root)
    path = (
        root
        / "config"
        / "v2"
        / "profiles"
        / f"{normalized}.json"
    )
    if not path.is_file():
        raise ConfigurationError(
            f"profile不存在：{normalized}",
            code="PROFILE_NOT_FOUND",
            details={"profile_id": normalized},
        )
    payload = load_json_object(path)
    strategy = payload.get("strategy")
    if not isinstance(strategy, dict):
        raise ConfigurationError(
            "profile.strategy必须是对象",
            code="PROFILE_INVALID",
        )
    profile = Profile(
        schema_version=_required_string(
            payload,
            "schema_version",
            code="PROFILE_INVALID",
        ),
        profile_id=_required_string(
            payload,
            "profile_id",
            code="PROFILE_INVALID",
        ),
        enabled=payload.get("enabled") is True,
        broker=_required_string(
            payload,
            "broker",
            code="PROFILE_INVALID",
        ),
        environment=_required_string(
            payload,
            "environment",
            code="PROFILE_INVALID",
        ),
        credential_key_env=_required_string(
            payload,
            "credential_key_env",
            code="PROFILE_INVALID",
        ),
        credential_secret_env=_required_string(
            payload,
            "credential_secret_env",
            code="PROFILE_INVALID",
        ),
        strategy_id=_required_string(
            strategy,
            "strategy_id",
            code="PROFILE_INVALID",
        ),
        strategy_version=_required_string(
            strategy,
            "strategy_version",
            code="PROFILE_INVALID",
        ),
        risk_profile=_required_string(
            payload,
            "risk_profile",
            code="PROFILE_INVALID",
        ),
        source_path=path,
    )
    profile.validate()
    if profile.profile_id != normalized:
        raise ConfigurationError(
            "profile文件名与profile_id不一致",
            code="PROFILE_ID_MISMATCH",
        )
    if require_enabled and not profile.enabled:
        raise ConfigurationError(
            f"profile已禁用：{normalized}",
            code="PROFILE_DISABLED",
            details={"profile_id": normalized},
        )
    return profile


def load_risk_profile(
    reference: str,
    *,
    project_root: Path | None = None,
) -> RiskProfile:
    match = RISK_REFERENCE.fullmatch(
        str(reference).strip()
    )
    if match is None:
        raise ConfigurationError(
            "risk profile引用格式无效",
            code="RISK_PROFILE_REFERENCE_INVALID",
        )
    risk_id, version = match.groups()
    path = (
        _project_root(project_root)
        / "config"
        / "v2"
        / "risk_profiles"
        / f"{risk_id}-{version}.json"
    )
    if not path.is_file():
        raise ConfigurationError(
            f"risk profile不存在：{reference}",
            code="RISK_PROFILE_NOT_FOUND",
        )
    payload = load_json_object(path)
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ConfigurationError(
            "risk profile settings必须是对象",
            code="RISK_PROFILE_INVALID",
        )
    result = RiskProfile(
        schema_version=_required_string(
            payload,
            "schema_version",
            code="RISK_PROFILE_INVALID",
        ),
        risk_profile_id=_required_string(
            payload,
            "risk_profile_id",
            code="RISK_PROFILE_INVALID",
        ),
        risk_profile_version=_required_string(
            payload,
            "risk_profile_version",
            code="RISK_PROFILE_INVALID",
        ),
        environment=_required_string(
            payload,
            "environment",
            code="RISK_PROFILE_INVALID",
        ),
        settings=settings,
        source_path=path,
    )
    if (
        result.schema_version != "1.0"
        or result.reference != reference
    ):
        raise ConfigurationError(
            "risk profile内容与引用不一致",
            code="RISK_PROFILE_INVALID",
        )
    return result


def account_id_hash(account_id: object) -> str:
    if account_id is None:
        raise ConfigurationError(
            "Alpaca account id为空",
            code="ACCOUNT_ID_MISSING",
        )
    normalized = str(account_id).strip()
    if not normalized:
        raise ConfigurationError(
            "Alpaca account id为空",
            code="ACCOUNT_ID_MISSING",
        )
    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def account_binding_path(
    profile_id: str,
    *,
    project_root: Path | None = None,
) -> Path:
    if not PROFILE_COMPONENT.fullmatch(
        str(profile_id)
    ):
        raise ConfigurationError(
            "profile名称格式无效",
            code="PROFILE_INVALID",
        )
    return (
        _project_root(project_root)
        / "account_bindings"
        / f"{profile_id}.json"
    )


def verify_or_bind_account(
    profile: Profile,
    account_id: object,
    *,
    bind_account: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Verify an account without ever persisting or reporting its raw id."""

    digest = account_id_hash(account_id)
    path = account_binding_path(
        profile.profile_id,
        project_root=project_root,
    )
    now = utc_now_iso()
    if not path.exists():
        if not bind_account:
            raise ConfigurationError(
                "profile尚未绑定账户；请核对hash后使用--bind-account",
                code="ACCOUNT_BINDING_REQUIRED",
                details={
                    "profile_id": profile.profile_id,
                    "account_id_hash": digest,
                },
            )
        payload = {
            "schema_version": "1.0",
            "profile_id": profile.profile_id,
            "environment": profile.environment,
            "account_id_hash": digest,
            "bound_at": now,
            "last_verified_at": now,
        }
        atomic_write_json(path, payload)
        return payload

    payload = load_json_object(path)
    if (
        payload.get("profile_id")
        != profile.profile_id
        or payload.get("environment")
        != profile.environment
    ):
        raise ConfigurationError(
            "账户绑定文件身份不匹配",
            code="ACCOUNT_BINDING_INVALID",
        )
    if payload.get("account_id_hash") != digest:
        raise ConfigurationError(
            "Alpaca账户hash与已绑定账户不一致",
            code="ACCOUNT_BINDING_MISMATCH",
            details={"profile_id": profile.profile_id},
        )
    payload["last_verified_at"] = now
    atomic_write_json(path, payload)
    return payload
