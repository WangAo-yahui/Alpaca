"""采集并不可变保存每个轮次的 initial guidance。

作用：统一处理 CLI、TTY、无人值守模式、文本规范化和 SHA-256 身份。
重要性：guidance 会影响三个决策阶段和 coarse revision，恢复轮次时绝不能静默改变。
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict, dataclass
from typing import Callable, TextIO

from v2.cli import CLIOptions
from v2.exceptions import ConfigurationError
from v2.runtime import (
    CyclePaths,
    atomic_write_json,
    load_json_object,
    new_york_now_iso,
    utc_now_iso,
)


GUIDANCE_SCHEMA_VERSION = "1.0"
GUIDANCE_STAGES = (
    "coarse",
    "portfolio",
    "execution",
)
GUIDANCE_MODES = {
    "cli",
    "prompt",
    "reviewed_no_comment",
    "skipped_by_flag",
}


def normalize_guidance_text(value: str) -> str:
    return (
        str(value)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def guidance_hash(value: str) -> str:
    return hashlib.sha256(
        normalize_guidance_text(value).encode(
            "utf-8"
        )
    ).hexdigest()


@dataclass(frozen=True)
class InitialGuidance:
    schema_version: str
    profile_id: str
    strategy_id: str
    strategy_version: str
    run_date: str
    cycle_id: str
    mode: str
    raw_text: str
    guidance_hash: str
    applies_to: tuple[str, ...]
    created_at: str
    created_at_new_york: str

    def validate(self) -> None:
        if self.schema_version != GUIDANCE_SCHEMA_VERSION:
            raise ConfigurationError(
                "initial guidance schema_version不支持",
                code="INITIAL_GUIDANCE_INVALID",
            )
        if self.mode not in GUIDANCE_MODES:
            raise ConfigurationError(
                "initial guidance mode无效",
                code="INITIAL_GUIDANCE_INVALID",
            )
        if self.applies_to != GUIDANCE_STAGES:
            raise ConfigurationError(
                "initial guidance applies_to必须覆盖三个阶段",
                code="INITIAL_GUIDANCE_INVALID",
            )
        if self.raw_text != normalize_guidance_text(
            self.raw_text
        ):
            raise ConfigurationError(
                "initial guidance文本未规范化",
                code="INITIAL_GUIDANCE_INVALID",
            )
        if self.guidance_hash != guidance_hash(
            self.raw_text
        ):
            raise ConfigurationError(
                "initial guidance hash不匹配",
                code="INITIAL_GUIDANCE_HASH_MISMATCH",
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        payload = asdict(self)
        payload["applies_to"] = list(
            self.applies_to
        )
        return payload

    def state_payload(
        self,
        paths: CyclePaths,
    ) -> dict[str, str]:
        return {
            "path": str(paths.initial_guidance),
            "guidance_hash": self.guidance_hash,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, object],
    ) -> "InitialGuidance":
        applies = payload.get("applies_to", [])
        result = cls(
            schema_version=str(
                payload.get("schema_version", "")
            ),
            profile_id=str(
                payload.get("profile_id", "")
            ),
            strategy_id=str(
                payload.get("strategy_id", "")
            ),
            strategy_version=str(
                payload.get("strategy_version", "")
            ),
            run_date=str(
                payload.get("run_date", "")
            ),
            cycle_id=str(
                payload.get("cycle_id", "")
            ),
            mode=str(payload.get("mode", "")),
            raw_text=str(
                payload.get("raw_text", "")
            ),
            guidance_hash=str(
                payload.get("guidance_hash", "")
            ),
            applies_to=tuple(
                str(value)
                for value in applies
            )
            if isinstance(applies, list)
            else (),
            created_at=str(
                payload.get("created_at", "")
            ),
            created_at_new_york=str(
                payload.get(
                    "created_at_new_york",
                    "",
                )
            ),
        )
        result.validate()
        return result


def load_initial_guidance(
    paths: CyclePaths,
) -> InitialGuidance:
    guidance = InitialGuidance.from_dict(
        load_json_object(
            paths.initial_guidance
        )
    )
    expected = (
        paths.profile_id,
        paths.strategy_id,
        paths.strategy_version,
        paths.run_date,
        paths.cycle_id,
    )
    actual = (
        guidance.profile_id,
        guidance.strategy_id,
        guidance.strategy_version,
        guidance.run_date,
        guidance.cycle_id,
    )
    if actual != expected:
        raise ConfigurationError(
            "initial guidance身份与当前轮次不一致",
            code="INITIAL_GUIDANCE_IDENTITY_MISMATCH",
        )
    return guidance


def collect_initial_guidance(
    options: CLIOptions,
    paths: CyclePaths,
    *,
    input_func: Callable[[str], str] = input,
    stdin: TextIO | None = None,
) -> InitialGuidance:
    """Resolve explicit/interactive guidance before any decision stage."""

    if paths.initial_guidance.exists():
        existing = load_initial_guidance(paths)
        if (
            options.guidance is not None
            and guidance_hash(options.guidance)
            != existing.guidance_hash
        ):
            raise ConfigurationError(
                "当前轮次已有不同的initial guidance；"
                "请使用--new-cycle创建新修订",
                code="INITIAL_GUIDANCE_RESUME_MISMATCH",
            )
        if (
            (options.no_guidance or options.unattended)
            and existing.raw_text
        ):
            raise ConfigurationError(
                "当前轮次已有initial guidance，"
                "不能在恢复时改为跳过；请使用--new-cycle",
                code="INITIAL_GUIDANCE_RESUME_MISMATCH",
            )
        return existing

    if options.guidance is not None:
        raw_text = normalize_guidance_text(
            options.guidance
        )
        mode = "cli"
    elif options.no_guidance or options.unattended:
        raw_text = ""
        mode = "skipped_by_flag"
    else:
        stream = stdin or sys.stdin
        if not stream.isatty():
            raise ConfigurationError(
                "非交互运行必须明确初始建议；"
                "请使用--guidance、--no-guidance或--unattended",
                code="INITIAL_GUIDANCE_REQUIRED_NONINTERACTIVE",
            )
        raw_text = normalize_guidance_text(
            input_func(
                "请输入贯穿粗选、组合与执行阶段的建议"
                "（可留空后回车）："
            )
        )
        mode = (
            "prompt"
            if raw_text
            else "reviewed_no_comment"
        )

    guidance = InitialGuidance(
        schema_version=GUIDANCE_SCHEMA_VERSION,
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=(
            paths.strategy_version
        ),
        run_date=paths.run_date,
        cycle_id=paths.cycle_id,
        mode=mode,
        raw_text=raw_text,
        guidance_hash=guidance_hash(raw_text),
        applies_to=GUIDANCE_STAGES,
        created_at=utc_now_iso(),
        created_at_new_york=(
            new_york_now_iso()
        ),
    )
    atomic_write_json(
        paths.initial_guidance,
        guidance.to_dict(),
    )
    return guidance
