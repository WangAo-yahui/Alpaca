"""解析并验证不可变的策略 release。

作用：校验 prompt、schema、策略配置的文件集合与 SHA-256，并记录 Git/release 身份。
重要性：任何原地修改都会改变策略行为，因此必须在运行 Codex 前失败并阻止继续。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from v2.exceptions import ConfigurationError
from v2.runtime import load_json_object


APP_VERSION = "2.0.0"


@dataclass(frozen=True)
class StrategyRelease:
    strategy_id: str
    strategy_version: str
    compatible_app_version: str
    description: str
    prompt_hashes: Mapping[str, str]
    schema_hashes: Mapping[str, str]
    config_hashes: Mapping[str, str]
    root: Path
    manifest_path: Path

    @property
    def release_hash(self) -> str:
        payload = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "compatible_app_version": (
                self.compatible_app_version
            ),
            "prompt_hashes": dict(
                sorted(self.prompt_hashes.items())
            ),
            "schema_hashes": dict(
                sorted(self.schema_hashes.items())
            ),
            "config_hashes": dict(
                sorted(self.config_hashes.items())
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def state_payload(
        self,
        *,
        risk_profile: str,
        git_commit: str,
    ) -> dict[str, Any]:
        return {
            "app_version": APP_VERSION,
            "git_commit": git_commit,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "risk_profile": risk_profile,
            "release_hash": self.release_hash,
            "prompt_hashes": dict(
                self.prompt_hashes
            ),
            "schema_hashes": dict(
                self.schema_hashes
            ),
            "config_hashes": dict(
                self.config_hashes
            ),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_map(
    payload: Mapping[str, Any],
    key: str,
) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(
            f"strategy manifest {key}必须是对象",
            code="STRATEGY_RELEASE_MANIFEST_INVALID",
        )
    result: dict[str, str] = {}
    for raw_path, raw_hash in value.items():
        relative = str(raw_path)
        digest = str(raw_hash)
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or len(digest) != 64
        ):
            raise ConfigurationError(
                f"strategy manifest {key}条目无效",
                code="STRATEGY_RELEASE_MANIFEST_INVALID",
            )
        result[relative] = digest
    return result


def _compatible_app(specifier: str) -> bool:
    # C.5 pins the entire 2.x application series.  Avoid a packaging
    # dependency for this small, deliberately narrow compatibility contract.
    return specifier == ">=2.0.0,<3.0.0"


def load_strategy_release(
    strategy_id: str,
    strategy_version: str,
    *,
    project_root: Path | None = None,
    verify_hashes: bool = True,
) -> StrategyRelease:
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    release_root = (
        root
        / "strategies"
        / strategy_id
        / strategy_version
    )
    manifest_path = release_root / "manifest.json"
    if not manifest_path.is_file():
        raise ConfigurationError(
            "strategy release不存在："
            f"{strategy_id}@{strategy_version}",
            code="STRATEGY_RELEASE_NOT_FOUND",
        )
    payload = load_json_object(manifest_path)
    manifest_strategy = str(
        payload.get("strategy_id", "")
    )
    manifest_version = str(
        payload.get("strategy_version", "")
    )
    compatible = str(
        payload.get(
            "compatible_app_version",
            "",
        )
    )
    if (
        manifest_strategy != strategy_id
        or manifest_version != strategy_version
    ):
        raise ConfigurationError(
            "strategy release manifest身份不一致",
            code="STRATEGY_RELEASE_IDENTITY_MISMATCH",
        )
    if not _compatible_app(compatible):
        raise ConfigurationError(
            "strategy release与当前app版本不兼容",
            code="STRATEGY_RELEASE_INCOMPATIBLE",
        )
    release = StrategyRelease(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        compatible_app_version=compatible,
        description=str(
            payload.get("description", "")
        ),
        prompt_hashes=_hash_map(
            payload,
            "prompt_hashes",
        ),
        schema_hashes=_hash_map(
            payload,
            "schema_hashes",
        ),
        config_hashes=_hash_map(
            payload,
            "config_hashes",
        ),
        root=release_root,
        manifest_path=manifest_path,
    )
    if verify_hashes:
        verify_strategy_release(release)
    return release


def verify_strategy_release(
    release: StrategyRelease,
) -> None:
    expected_paths = {
        *release.prompt_hashes.keys(),
        *release.schema_hashes.keys(),
        *release.config_hashes.keys(),
    }
    actual_paths = {
        path.relative_to(release.root).as_posix()
        for directory_name in (
            "prompts",
            "schemas",
            "config",
        )
        for path in (
            release.root / directory_name
        ).rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise ConfigurationError(
            "strategy release文件集合与manifest不一致",
            code="STRATEGY_RELEASE_FILE_SET_MISMATCH",
            details={
                "missing": sorted(
                    expected_paths - actual_paths
                ),
                "unexpected": sorted(
                    actual_paths - expected_paths
                ),
            },
        )
    for group, hashes in (
        ("prompt", release.prompt_hashes),
        ("schema", release.schema_hashes),
        ("config", release.config_hashes),
    ):
        for relative, expected in hashes.items():
            path = release.root / relative
            if (
                not path.is_file()
                or sha256_file(path) != expected
            ):
                raise ConfigurationError(
                    "strategy release内容已被修改："
                    f"{relative}",
                    code="STRATEGY_RELEASE_HASH_MISMATCH",
                    details={
                        "artifact_group": group,
                        "relative_path": relative,
                    },
                )


def get_git_commit(
    project_root: Path,
) -> tuple[str, bool]:
    """Return commit and whether the value is verified."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip()
        if len(value) == 40:
            return value, True
    except (
        OSError,
        subprocess.SubprocessError,
    ):
        pass
    return "unknown", False
