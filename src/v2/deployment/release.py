"""构建、验证并原子安装 Stage H 不可变应用 release。

作用：只复制运行所需的已跟踪代码、配置、schema、prompt 和策略，并记录每个文件 SHA-256。
重要性：`.env`、账户绑定、runtime、reports、market data 和 logs 永远不能进入 release。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from v2.deployment.paths import DeploymentPaths


INCLUDED_PREFIXES = (
    "src/v2/",
    "config/v2/",
    "config/universe/",
    "schemas/v2/",
    "strategies/",
    "prompts/v2/",
)
INCLUDED_FILES = frozenset(
    {"requirements.txt", "requirements.lock", "wa"}
)
FORBIDDEN_ROOTS = frozenset(
    {
        ".env",
        "account_bindings",
        "decision_runtime_v2",
        "reports",
        "shared_data",
        "var",
        ".git",
        ".Alpaca",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.tmp"
    )
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise ValueError(f"JSON顶层不是对象：{path}")
    return payload


def _included(relative: str) -> bool:
    path = Path(relative)
    if (
        relative.startswith(".env")
        or not path.parts
        or path.parts[0] in FORBIDDEN_ROOTS
    ):
        return False
    return (
        relative in INCLUDED_FILES
        or any(
            relative.startswith(prefix)
            for prefix in INCLUDED_PREFIXES
        )
    )


def _tracked_files(project_root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "ls-files",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    values = result.stdout.decode("utf-8").split("\0")
    return tuple(
        sorted(
            value
            for value in values
            if value and _included(value)
        )
    )


def source_tree_fingerprint(
    project_root: Path,
) -> str:
    """Hash every runnable source file, including uncommitted additions."""

    root = project_root.expanduser().resolve()
    paths: set[Path] = set()
    for relative in INCLUDED_FILES:
        candidate = root / relative
        if candidate.is_file():
            paths.add(candidate)
    for prefix in INCLUDED_PREFIXES:
        directory = root / prefix.rstrip("/")
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*"):
            if (
                candidate.is_file()
                and "__pycache__"
                not in candidate.parts
                and candidate.suffix != ".pyc"
                and candidate.name != ".DS_Store"
            ):
                paths.add(candidate)
    digest = hashlib.sha256()
    for path in sorted(
        paths,
        key=lambda item: (
            item.relative_to(root).as_posix()
        ),
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            sha256_file(path).encode("ascii")
        )
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleaseArtifact:
    release_id: str
    git_commit: str
    root: Path
    manifest: Path
    manifest_hash: str


class ReleaseBuilder:
    def __init__(self, paths: DeploymentPaths) -> None:
        self.paths = paths

    @staticmethod
    def release_id(git_commit: str) -> str:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{git_commit[:12]}"

    def build_staging(
        self,
        *,
        git_commit: str,
        release_id: str | None = None,
    ) -> ReleaseArtifact:
        identifier = release_id or self.release_id(
            git_commit
        )
        destination = self.paths.staging / identifier
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        source_files = _tracked_files(
            self.paths.project_root
        )
        missing_required = {
            "wa",
            "requirements.lock",
        } - set(source_files)
        if missing_required:
            raise ValueError(
                "release缺少已跟踪入口或依赖锁："
                + ",".join(sorted(missing_required))
            )
        for relative in source_files:
            source = self.paths.project_root / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError(
                    f"release源文件类型不安全：{relative}"
                )
            target = destination / relative
            target.parent.mkdir(
                parents=True, exist_ok=True
            )
            shutil.copy2(source, target)
        file_hashes = {
            relative: sha256_file(destination / relative)
            for relative in source_files
        }
        manifest = {
            "schema_version": "1.0",
            "release_id": identifier,
            "git_commit": git_commit,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "profile_id": self.paths.profile_id,
            "environment": self.paths.environment,
            "file_hashes": file_hashes,
            "excluded_sensitive_roots": sorted(
                FORBIDDEN_ROOTS
            ),
        }
        manifest_path = (
            destination / "release_manifest.json"
        )
        atomic_write_json(manifest_path, manifest)
        artifact = ReleaseArtifact(
            release_id=identifier,
            git_commit=git_commit,
            root=destination,
            manifest=manifest_path,
            manifest_hash=sha256_file(manifest_path),
        )
        self.validate(artifact.root)
        return artifact

    def validate(self, root: Path) -> ReleaseArtifact:
        release_root = root.expanduser().resolve()
        manifest_path = (
            release_root / "release_manifest.json"
        )
        if not manifest_path.is_file():
            raise ValueError("release manifest不存在")
        manifest = load_json(manifest_path)
        hashes = manifest.get("file_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("release manifest缺少file_hashes")
        expected = {
            str(relative)
            for relative in hashes
        }
        actual = {
            path.relative_to(
                release_root
            ).as_posix()
            for path in release_root.rglob("*")
            if path.is_file()
            and path.name != "release_manifest.json"
        }
        if expected != actual:
            raise ValueError(
                "release文件集合与manifest不一致"
            )
        for relative, digest in hashes.items():
            if (
                not _included(str(relative))
                or not isinstance(digest, str)
                or len(digest) != 64
                or sha256_file(
                    release_root / str(relative)
                )
                != digest
            ):
                raise ValueError(
                    f"release文件hash无效：{relative}"
                )
        for path in release_root.rglob("*"):
            relative = path.relative_to(release_root)
            if (
                relative.name.startswith(".env")
                or relative.parts[0] in FORBIDDEN_ROOTS
            ):
                raise ValueError(
                    f"release包含敏感路径：{path.name}"
                )
        return ReleaseArtifact(
            release_id=str(manifest["release_id"]),
            git_commit=str(manifest["git_commit"]),
            root=release_root,
            manifest=manifest_path,
            manifest_hash=sha256_file(manifest_path),
        )

    def install(
        self,
        artifact: ReleaseArtifact,
    ) -> ReleaseArtifact:
        destination = (
            self.paths.releases / artifact.release_id
        )
        if destination.exists():
            raise FileExistsError(
                f"release已存在：{artifact.release_id}"
            )
        os.replace(artifact.root, destination)
        installed = self.validate(destination)
        for path in destination.rglob("*"):
            if path.is_file():
                path.chmod(
                    0o555
                    if path.relative_to(
                        destination
                    ).as_posix()
                    == "wa"
                    else 0o444
                )
        return installed


def manifests_contain_forbidden_text(
    roots: Iterable[Path],
    forbidden_values: Iterable[str],
) -> bool:
    values = tuple(
        value for value in forbidden_values if value
    )
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if any(value in content for value in values):
                return True
    return False
