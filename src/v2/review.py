"""采集并验证 Stage D 组合决策后的人工补充意见。

作用：在进入未来执行阶段前保存一次原始用户评论，或明确记录无人值守跳过。
重要性：该文件是执行阶段可审计的人为约束来源；Stage D 不用 AI 解释或改写原文。
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


REVIEW_PROMPT = "请输入本轮执行前补充意见，直接回车继续："


def normalize_review_text(value: str) -> str:
    return (
        str(value)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def review_hash(value: str) -> str:
    return hashlib.sha256(
        normalize_review_text(value).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class UserReview:
    schema_version: str
    profile_id: str
    strategy_id: str
    strategy_version: str
    run_date: str
    cycle_id: str
    mode: str
    raw_comment: str
    review_hash: str
    constraints: tuple[str, ...]
    prohibitions: tuple[str, ...]
    preferences: tuple[str, ...]
    trade_requests: tuple[str, ...]
    applies_to: tuple[str, ...]
    created_at: str
    created_at_new_york: str

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ConfigurationError(
                "user review schema_version不支持",
                code="USER_REVIEW_INVALID",
            )
        if self.mode not in {
            "user_comment",
            "skipped_by_flag",
        }:
            raise ConfigurationError(
                "user review mode无效",
                code="USER_REVIEW_INVALID",
            )
        if (
            self.mode == "skipped_by_flag"
            and self.raw_comment
        ):
            raise ConfigurationError(
                "跳过review时raw_comment必须为空",
                code="USER_REVIEW_INVALID",
            )
        if self.raw_comment != normalize_review_text(
            self.raw_comment
        ):
            raise ConfigurationError(
                "user review文本未规范化",
                code="USER_REVIEW_INVALID",
            )
        if self.review_hash != review_hash(
            self.raw_comment
        ):
            raise ConfigurationError(
                "user review hash不匹配",
                code="USER_REVIEW_HASH_MISMATCH",
            )
        if self.applies_to != ("execution",):
            raise ConfigurationError(
                "user review只允许作用于execution",
                code="USER_REVIEW_INVALID",
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        payload = asdict(self)
        for field in (
            "constraints",
            "prohibitions",
            "preferences",
            "trade_requests",
            "applies_to",
        ):
            payload[field] = list(payload[field])
        return payload

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, object],
    ) -> "UserReview":
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
            raw_comment=str(
                payload.get("raw_comment", "")
            ),
            review_hash=str(
                payload.get("review_hash", "")
            ),
            constraints=tuple(
                str(value)
                for value in payload.get(
                    "constraints",
                    [],
                )
            ),
            prohibitions=tuple(
                str(value)
                for value in payload.get(
                    "prohibitions",
                    [],
                )
            ),
            preferences=tuple(
                str(value)
                for value in payload.get(
                    "preferences",
                    [],
                )
            ),
            trade_requests=tuple(
                str(value)
                for value in payload.get(
                    "trade_requests",
                    [],
                )
            ),
            applies_to=tuple(
                str(value)
                for value in payload.get(
                    "applies_to",
                    [],
                )
            ),
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


def _new_review(
    paths: CyclePaths,
    *,
    mode: str,
    raw_comment: str,
) -> UserReview:
    normalized = normalize_review_text(
        raw_comment
    )
    return UserReview(
        schema_version="1.0",
        profile_id=paths.profile_id,
        strategy_id=paths.strategy_id,
        strategy_version=(
            paths.strategy_version
        ),
        run_date=paths.run_date,
        cycle_id=paths.cycle_id,
        mode=mode,
        raw_comment=normalized,
        review_hash=review_hash(normalized),
        constraints=(),
        prohibitions=(),
        preferences=(),
        trade_requests=(),
        applies_to=("execution",),
        created_at=utc_now_iso(),
        created_at_new_york=(
            new_york_now_iso()
        ),
    )


def load_user_review(
    paths: CyclePaths,
) -> UserReview:
    review = UserReview.from_dict(
        load_json_object(paths.user_review)
    )
    expected = (
        paths.profile_id,
        paths.strategy_id,
        paths.strategy_version,
        paths.run_date,
        paths.cycle_id,
    )
    actual = (
        review.profile_id,
        review.strategy_id,
        review.strategy_version,
        review.run_date,
        review.cycle_id,
    )
    if actual != expected:
        raise ConfigurationError(
            "user review身份与当前轮次不一致",
            code="USER_REVIEW_IDENTITY_MISMATCH",
        )
    return review


def write_skipped_review(
    paths: CyclePaths,
) -> UserReview:
    review = _new_review(
        paths,
        mode="skipped_by_flag",
        raw_comment="",
    )
    atomic_write_json(
        paths.user_review,
        review.to_dict(),
    )
    return review


def collect_user_review(
    options: CLIOptions,
    paths: CyclePaths,
    *,
    input_func: Callable[[str], str] = input,
    stdin: TextIO | None = None,
) -> UserReview:
    """Read exactly one post-portfolio comment or record an explicit skip."""

    if paths.user_review.is_file():
        return load_user_review(paths)
    if (
        options.no_review
        or options.unattended
    ):
        return write_skipped_review(paths)
    stream = stdin or sys.stdin
    if not stream.isatty():
        raise ConfigurationError(
            "非交互运行需要使用--no-review或--unattended",
            code="USER_REVIEW_REQUIRED_NONINTERACTIVE",
        )
    raw_comment = input_func(REVIEW_PROMPT)
    review = _new_review(
        paths,
        mode="user_comment",
        raw_comment=raw_comment,
    )
    atomic_write_json(
        paths.user_review,
        review.to_dict(),
    )
    return review
