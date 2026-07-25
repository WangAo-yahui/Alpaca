"""原子持久化 Stage G 的每一个 paper 写操作状态。

作用：在券商写前、响应后、查询确认后逐次保存 intent 对应的操作日志。
重要性：网络超时或进程中断后只能依据此日志恢复；request_started 绝不能被当作可安全重试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from v2.models.submission import (
    SubmissionOperation,
    SubmissionOperationState,
)
from v2.runtime import (
    atomic_write_json,
    load_json_object,
    utc_now_iso,
)


class SubmissionJournal:
    def __init__(
        self,
        path: Path,
        *,
        profile_id: str,
        run_date: str,
        cycle_id: str,
    ) -> None:
        self.path = path
        self.profile_id = profile_id
        self.run_date = run_date
        self.cycle_id = cycle_id
        self.operations: list[SubmissionOperation] = []
        self.created_at = utc_now_iso()

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        profile_id: str,
        run_date: str,
        cycle_id: str,
        operations: list[SubmissionOperation] | None = None,
    ) -> "SubmissionJournal":
        journal = cls(
            path,
            profile_id=profile_id,
            run_date=run_date,
            cycle_id=cycle_id,
        )
        if path.is_file():
            payload = load_json_object(path)
            if (
                payload.get("profile_id") != profile_id
                or payload.get("run_date") != run_date
                or payload.get("cycle_id") != cycle_id
            ):
                raise ValueError("submission journal身份不一致")
            journal.created_at = str(
                payload.get("created_at", journal.created_at)
            )
            journal.operations = [
                SubmissionOperation.from_dict(item)
                for item in payload.get("operations", [])
                if isinstance(item, dict)
            ]
            return journal
        journal.operations = list(operations or [])
        journal.save()
        return journal

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "environment": "paper",
            "run_date": self.run_date,
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "updated_at": utc_now_iso(),
            "has_uncertain_operation": self.has_uncertain,
            "operations": [
                operation.to_dict()
                for operation in self.operations
            ],
        }

    @property
    def has_uncertain(self) -> bool:
        return any(
            operation.state
            == SubmissionOperationState.UNCERTAIN
            for operation in self.operations
        )

    def save(self) -> None:
        atomic_write_json(self.path, self.to_dict())

    def replace_unstarted_submissions(
        self,
        operations: list[SubmissionOperation],
    ) -> None:
        """Replace only not-yet-written submit intents after a fresh replan."""

        if any(
            operation.operation_type.value != "submit"
            for operation in operations
        ):
            raise ValueError("replacement replan只接受submit操作")
        retained: list[SubmissionOperation] = []
        for operation in self.operations:
            if operation.operation_type.value != "submit":
                retained.append(operation)
                continue
            if (
                operation.attempt_count > 0
                or operation.request_started_at is not None
            ):
                raise ValueError(
                    "已有submit写尝试，禁止替换其持久化意图"
                )
        operation_ids = {
            operation.operation_id
            for operation in [*retained, *operations]
        }
        if len(operation_ids) != len(retained) + len(operations):
            raise ValueError("replacement replan产生重复operation_id")
        self.operations = [*retained, *operations]
        self.save()

    def get(self, operation_id: str) -> SubmissionOperation:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise KeyError(operation_id)

    def persist_before_write(
        self,
        operation_id: str,
    ) -> SubmissionOperation:
        operation = self.get(operation_id)
        if operation.state != SubmissionOperationState.PREPARED:
            raise ValueError(
                f"操作不是prepared：{operation_id}"
            )
        operation.state = SubmissionOperationState.REQUEST_STARTED
        operation.attempt_count += 1
        operation.request_started_at = utc_now_iso()
        operation.last_checked_at = operation.request_started_at
        self.save()
        return operation

    def transition(
        self,
        operation_id: str,
        state: SubmissionOperationState,
        **updates: Any,
    ) -> SubmissionOperation:
        operation = self.get(operation_id)
        for field_name, value in updates.items():
            if not hasattr(operation, field_name):
                raise ValueError(
                    f"未知journal字段：{field_name}"
                )
            setattr(operation, field_name, value)
        operation.state = state
        operation.last_checked_at = utc_now_iso()
        if state in {
            SubmissionOperationState.RESPONSE_RECEIVED,
            SubmissionOperationState.LOOKUP_CONFIRMED,
        }:
            operation.response_received_at = (
                operation.response_received_at
                or operation.last_checked_at
            )
        if state in {
            SubmissionOperationState.COMPLETED,
            SubmissionOperationState.FAILED_DEFINITE,
            SubmissionOperationState.UNCERTAIN,
            SubmissionOperationState.SKIPPED,
        }:
            operation.completed_at = operation.last_checked_at
        self.save()
        return operation
