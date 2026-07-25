import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import (
    Draft202012Validator,
    FormatChecker,
)

from runtime_paths import (
    build_runtime_paths,
    find_latest_stage_workspace,
    get_project_root,
)
from validate_portfolio_decision import (
    validate_portfolio_decision,
)


SCRIPT_VERSION = (
    "2026-07-22-portfolio-codex-runner-v1"
)

PROMPT_RELATIVE_PATH = Path(
    "prompts/portfolio_decision.md"
)

SCHEMA_RELATIVE_PATH = Path(
    "schemas/portfolio_decision.schema.json"
)

AGENTS_SOURCE_RELATIVE_PATH = Path(
    "prompts/portfolio_decision_AGENTS.md"
)

AGENTS_WORKSPACE_RELATIVE_PATH = Path(
    "AGENTS.md"
)

OUTPUT_RELATIVE_PATH = Path(
    "output/portfolio_decision.json"
)

DEFAULT_CODEX_TIMEOUT_MINUTES = 45

NETWORK_ERROR_PATTERNS = (
    "transport channel closed",
    "request timed out",
    "network error",
    "fatal network error",
    "error sending request",
    "http/request failed",
    "reconnecting...",
    "falling back from websockets",
    "backend-api/codex/alpha/search",
    "failed to refresh available models",
)

PROXY_ENVIRONMENT_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


@dataclass
class CodexRunResult:
    """一次Codex子进程调用结果。"""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    attempt_label: str


@dataclass
class ValidAttempt:
    """已经通过Schema和Python业务校验的尝试。"""

    output_path: Path
    output: dict[str, Any]
    validation: dict[str, Any]
    run_result: CodexRunResult


def load_json_object(
    path: Path,
) -> dict[str, Any]:
    """读取JSON对象。"""
    if not path.exists():
        raise FileNotFoundError(
            f"缺少文件：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON顶层必须是对象：{path}"
        )

    return payload


def save_json_atomically(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """原子保存JSON对象。"""
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    os.replace(
        temporary_path,
        path,
    )


def replace_contract_atomically(
    source: Path,
    destination: Path,
) -> None:
    """原子刷新可能已经只读的工作区契约文件。"""
    if not source.exists():
        raise FileNotFoundError(
            f"项目中缺少运行契约：{source}"
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        destination.parent
        / f".{destination.name}.runtime_refresh.tmp"
    )

    temporary_path.unlink(
        missing_ok=True
    )

    try:
        shutil.copy2(
            source,
            temporary_path,
        )

        try:
            os.chmod(
                temporary_path,
                0o644,
            )
        except OSError:
            pass

        os.replace(
            temporary_path,
            destination,
        )

        try:
            os.chmod(
                destination,
                0o444,
            )
        except OSError:
            pass

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


def copy_runtime_contracts(
    *,
    project_root: Path,
    workspace: Path,
) -> None:
    """把最新第二阶段契约安全刷新到工作区。"""
    mappings = (
        (
            project_root
            / PROMPT_RELATIVE_PATH,
            workspace
            / PROMPT_RELATIVE_PATH,
        ),
        (
            project_root
            / SCHEMA_RELATIVE_PATH,
            workspace
            / SCHEMA_RELATIVE_PATH,
        ),
        (
            project_root
            / AGENTS_SOURCE_RELATIVE_PATH,
            workspace
            / AGENTS_WORKSPACE_RELATIVE_PATH,
        ),
    )

    for source, destination in mappings:
        replace_contract_atomically(
            source=source,
            destination=destination,
        )


def strip_volatile_values(
    value: Any,
) -> Any:
    """移除不影响决策内容的生成时间字段。"""
    volatile_keys = {
        "generated_at",
        "validated_at",
        "downloaded_at",
        "saved_at",
        "updated_at",
    }

    if isinstance(value, dict):
        return {
            key: strip_volatile_values(
                child
            )
            for key, child in sorted(
                value.items()
            )
            if key not in volatile_keys
        }

    if isinstance(value, list):
        return [
            strip_volatile_values(child)
            for child in value
        ]

    return value


def update_hash_with_json(
    digest: Any,
    path: Path,
    relative_name: str,
) -> None:
    """把规范化JSON加入输入哈希。"""
    payload = load_json_object(path)

    encoded = json.dumps(
        strip_volatile_values(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest.update(
        relative_name.encode("utf-8")
    )
    digest.update(b"\0")
    digest.update(encoded)
    digest.update(b"\0")


def update_hash_with_file(
    digest: Any,
    path: Path,
    relative_name: str,
) -> None:
    """把普通文件加入输入哈希。"""
    if not path.exists():
        raise FileNotFoundError(
            f"组合决策输入缺失：{path}"
        )

    digest.update(
        relative_name.encode("utf-8")
    )
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")


def calculate_input_hash(
    workspace: Path,
) -> str:
    """计算第二阶段实质输入哈希。"""
    digest = hashlib.sha256()

    required_json_files = (
        Path(
            "data/snapshots/"
            "portfolio_input.json"
        ),
        Path(
            "data/snapshots/"
            "coarse_candidates.json"
        ),
        Path(
            "data/snapshots/account.json"
        ),
        Path(
            "data/snapshots/positions.json"
        ),
        Path(
            "data/snapshots/open_orders.json"
        ),
        Path(
            "config/"
            "daily_decision_policy.json"
        ),
        Path(
            "config/order_policy.json"
        ),
    )

    optional_json_files = (
        Path(
            "data/snapshots/"
            "today_orders.json"
        ),
        Path(
            "data/snapshots/assets.json"
        ),
        Path("config/screener.json"),
    )

    for relative_path in (
        required_json_files
    ):
        path = workspace / relative_path

        if not path.exists():
            raise FileNotFoundError(
                f"组合决策输入缺失：{path}"
            )

        update_hash_with_json(
            digest,
            path,
            relative_path.as_posix(),
        )

    for relative_path in (
        optional_json_files
    ):
        path = workspace / relative_path

        if path.exists():
            update_hash_with_json(
                digest,
                path,
                relative_path.as_posix(),
            )

    daily_directory = (
        workspace
        / "data"
        / "raw_bars"
        / "daily"
    )

    daily_files = sorted(
        daily_directory.glob("*.json")
    )

    if len(daily_files) != 60:
        raise ValueError(
            "第二阶段工作区日线文件必须为60个，"
            f"实际={len(daily_files)}"
        )

    for path in daily_files:
        update_hash_with_json(
            digest,
            path,
            path.relative_to(
                workspace
            ).as_posix(),
        )

    intraday_root = (
        workspace
        / "data"
        / "raw_bars"
        / "intraday"
    )

    intraday_files = sorted(
        intraday_root.glob("*/*.json")
    )

    if len(intraday_files) != 60:
        raise ValueError(
            "第二阶段工作区盘中文件必须为60个，"
            f"实际={len(intraday_files)}"
        )

    for path in intraday_files:
        update_hash_with_json(
            digest,
            path,
            path.relative_to(
                workspace
            ).as_posix(),
        )

    for relative_path in (
        PROMPT_RELATIVE_PATH,
        SCHEMA_RELATIVE_PATH,
        AGENTS_WORKSPACE_RELATIVE_PATH,
    ):
        update_hash_with_file(
            digest,
            workspace / relative_path,
            relative_path.as_posix(),
        )

    return digest.hexdigest()


def get_state_path_for_workspace(
    *,
    workspace: Path,
    project_root: Path,
) -> Path:
    """获取该工作区日期唯一状态文件路径。"""
    run_date = workspace.parent.name

    paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    expected_workspace = (
        paths.portfolio_workspace.resolve()
    )

    if workspace.resolve() != expected_workspace:
        raise ValueError(
            "组合决策工作区不符合统一路径："
            f"实际={workspace.resolve()}；"
            f"规范={expected_workspace}"
        )

    return paths.decision_state


def load_state(
    state_path: Path,
) -> dict[str, Any]:
    """读取当天统一运行状态。"""
    if not state_path.exists():
        return {
            "schema_version": "1.0",
            "run_date": (
                state_path.parent.name
            ),
            "updated_at": None,
            "stages": {},
        }

    return load_json_object(state_path)


def update_stage_state(
    state_path: Path,
    *,
    input_hash: str,
    status: str,
    output_hash: str | None = None,
    network_research_status: str | None = None,
    position_decision_count: int | None = None,
    positive_target_count: int | None = None,
    target_position_count: int | None = None,
    pending_order_review_count: int | None = None,
    attempt_count: int | None = None,
    retry_strategy: str | None = None,
    message: str | None = None,
) -> None:
    """更新portfolio_decision阶段状态。"""
    state = load_state(state_path)

    stages = state.setdefault(
        "stages",
        {},
    )

    if not isinstance(stages, dict):
        stages = {}
        state["stages"] = stages

    stages["portfolio_decision"] = {
        "status": status,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "network_research_status": (
            network_research_status
        ),
        "position_decision_count": (
            position_decision_count
        ),
        "positive_target_count": (
            positive_target_count
        ),
        "target_position_count": (
            target_position_count
        ),
        "pending_order_review_count": (
            pending_order_review_count
        ),
        "attempt_count": attempt_count,
        "retry_strategy": retry_strategy,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "message": message,
    }

    state["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    save_json_atomically(
        state_path,
        state,
    )


def sha256_file(
    path: Path,
) -> str:
    """计算文件SHA-256。"""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def validate_schema_file(
    *,
    output_path: Path,
    schema_path: Path,
) -> list[str]:
    """执行结构化输出Schema校验。"""
    output = load_json_object(
        output_path
    )
    schema = load_json_object(
        schema_path
    )

    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(output),
        key=lambda error: (
            list(error.absolute_path),
            error.message,
        ),
    )

    messages: list[str] = []

    for error in errors:
        path = ".".join(
            str(item)
            for item
            in error.absolute_path
        )

        messages.append(
            f"{path or '$'}："
            f"{error.message}"
        )

    return messages


def validate_openai_output_schema(
    schema: dict[str, Any],
) -> list[str]:
    """对Codex严格输出Schema做本地预检。"""
    errors: list[str] = []

    unsupported_keywords = {
        "uniqueItems",
        "default",
        "examples",
        "minContains",
        "maxContains",
        "contains",
        "unevaluatedProperties",
        "propertyNames",
        "patternProperties",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "format",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }

    def walk(
        node: Any,
        path: str,
    ) -> None:
        if isinstance(node, list):
            for index, item in enumerate(
                node
            ):
                walk(
                    item,
                    f"{path}[{index}]",
                )

            return

        if not isinstance(node, dict):
            return

        for keyword in (
            unsupported_keywords
        ):
            if keyword in node:
                errors.append(
                    f"{path}使用不支持的关键字："
                    f"{keyword}"
                )

        is_schema_node = (
            "properties" in node
            or "items" in node
            or "enum" in node
            or "$ref" in node
            or "type" in node
        )

        if (
            is_schema_node
            and "$ref" not in node
            and "type" not in node
        ):
            errors.append(
                f"{path}缺少显式type"
            )

        if node.get("type") == "object":
            properties = node.get(
                "properties",
                {},
            )
            required = node.get(
                "required",
                [],
            )

            if node.get(
                "additionalProperties"
            ) is not False:
                errors.append(
                    f"{path}必须设置"
                    "additionalProperties=false"
                )

            if not isinstance(
                properties,
                dict,
            ):
                errors.append(
                    f"{path}.properties必须是对象"
                )
            elif not isinstance(
                required,
                list,
            ):
                errors.append(
                    f"{path}.required必须是数组"
                )
            else:
                missing = sorted(
                    set(properties)
                    - set(required)
                )

                if missing:
                    errors.append(
                        f"{path}存在未列入required"
                        "的字段："
                        + ", ".join(missing)
                    )

        for key, value in node.items():
            if key in {
                "enum",
                "required",
            }:
                continue

            walk(
                value,
                f"{path}.{key}",
            )

    walk(schema, "$")

    return errors


def preflight_output_schema(
    schema_path: Path,
) -> None:
    """调用Codex前拒绝不兼容Schema。"""
    schema = load_json_object(
        schema_path
    )

    errors = (
        validate_openai_output_schema(
            schema
        )
    )

    if errors:
        raise RuntimeError(
            "Codex输出Schema本地预检失败：\n- "
            + "\n- ".join(errors)
        )


def build_prompt(
    workspace: Path,
) -> str:
    """构建第二阶段最终调用指令。"""
    base_prompt = (
        workspace
        / PROMPT_RELATIVE_PATH
    ).read_text(
        encoding="utf-8"
    )

    launcher_instruction = """
## 自动调用器补充约束

本次由Python非交互调用器启动。

- 最终回复本身必须是严格符合
  `schemas/portfolio_decision.schema.json`
  的单个JSON对象。
- 不得通过shell、Python或编辑工具直接创建或覆盖
  `output/portfolio_decision.json`。
- 只允许在`.tmp/codex/`创建当前调用需要的临时计算文件。
- 不得写入报告、研究文件、项目源码或其他持久结果。
- Python会先校验临时结果，再原子更新唯一的正式输出。
- 不得输出最终可执行订单、下单数量或
  `execution_new_position_allowed`。
- 不要在JSON前后添加Markdown、代码围栏或解释文字。
"""

    return (
        base_prompt.rstrip()
        + "\n\n"
        + launcher_instruction.strip()
        + "\n"
    )


def has_network_failure(
    *texts: str,
) -> bool:
    """识别网络、搜索或传输故障。"""
    combined = "\n".join(
        text.lower()
        for text in texts
        if text
    )

    return any(
        pattern in combined
        for pattern
        in NETWORK_ERROR_PATTERNS
    )


def build_child_environment(
    *,
    clean_proxy_environment: bool,
) -> tuple[
    dict[str, str],
    list[str],
]:
    """构建Codex子进程环境。"""
    environment = os.environ.copy()

    present_names = [
        name
        for name
        in PROXY_ENVIRONMENT_NAMES
        if environment.get(name)
    ]

    if clean_proxy_environment:
        for name in (
            PROXY_ENVIRONMENT_NAMES
        ):
            environment.pop(
                name,
                None,
            )

    return environment, present_names


def stream_pipe(
    stream: Any,
    target: Any,
    collector: list[str],
) -> None:
    """实时转发并收集子进程日志。"""
    try:
        for line in iter(
            stream.readline,
            "",
        ):
            collector.append(line)
            print(
                line,
                end="",
                file=target,
                flush=True,
            )

    finally:
        stream.close()


def build_codex_command(
    *,
    workspace: Path,
    temp_output_path: Path,
) -> list[str]:
    """构建Codex CLI命令。"""
    codex_path = shutil.which("codex")

    if codex_path is None:
        raise FileNotFoundError(
            "系统中找不到codex命令"
        )

    return [
        codex_path,
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "--search",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--output-schema",
        str(
            workspace
            / SCHEMA_RELATIVE_PATH
        ),
        "--output-last-message",
        str(temp_output_path),
        "-",
    ]


def run_codex_attempt(
    *,
    workspace: Path,
    prompt: str,
    temp_output_path: Path,
    timeout_seconds: float,
    attempt_label: str,
    clean_proxy_environment: bool,
) -> CodexRunResult:
    """执行一次带超时的Codex调用。"""
    temp_output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_output_path.unlink(
        missing_ok=True
    )

    command = build_codex_command(
        workspace=workspace,
        temp_output_path=temp_output_path,
    )

    (
        environment,
        present_proxy_names,
    ) = build_child_environment(
        clean_proxy_environment=(
            clean_proxy_environment
        )
    )

    print()
    print(
        f"Codex调用尝试：{attempt_label}"
    )
    print(f"工作区：{workspace}")
    print("阶段：第二阶段组合与仓位决策")
    print("网络：启用Codex实时Web搜索")
    print(
        "文件权限：仅允许当前工作区，"
        "持久结果由Python写入"
    )
    print(
        "检测到的代理/证书环境变量名称："
        + (
            ", ".join(
                present_proxy_names
            )
            if present_proxy_names
            else "无"
        )
    )
    print(
        "本次子进程代理处理："
        + (
            "临时移除代理及自定义证书变量"
            if clean_proxy_environment
            else "继承当前终端环境"
        )
    )

    process = subprocess.Popen(
        command,
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
    )

    if (
        process.stdin is None
        or process.stdout is None
        or process.stderr is None
    ):
        process.kill()

        raise RuntimeError(
            "无法建立Codex子进程管道"
        )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    stdout_thread = threading.Thread(
        target=stream_pipe,
        args=(
            process.stdout,
            sys.stdout,
            stdout_lines,
        ),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stream_pipe,
        args=(
            process.stderr,
            sys.stderr,
            stderr_lines,
        ),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    process.stdin.write(prompt)
    process.stdin.close()

    timed_out = False

    try:
        returncode = process.wait(
            timeout=timeout_seconds
        )

    except subprocess.TimeoutExpired:
        timed_out = True

        print()
        print(
            f"Codex调用超过"
            f"{timeout_seconds / 60:.0f}分钟，"
            "正在终止"
        )

        process.terminate()

        try:
            returncode = process.wait(
                timeout=10
            )

        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    return CodexRunResult(
        returncode=returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        timed_out=timed_out,
        attempt_label=attempt_label,
    )


def print_validation_result(
    validation: dict[str, Any],
    *,
    heading: str,
) -> None:
    """打印第二阶段校验结果。"""
    print()
    print(heading)
    print(
        "逐标的决策数量："
        f"{validation.get('position_decision_count')}"
    )
    print(
        "正目标权重数量："
        f"{validation.get('positive_target_count')}"
    )
    print(
        "声明目标持仓数量："
        f"{validation.get('target_position_count')}"
    )
    print(
        "挂单复核数量："
        f"{validation.get('pending_order_review_count')}"
    )
    print(
        "联网状态："
        f"{validation.get('network_status')}"
    )

    warnings = validation.get(
        "warnings",
        [],
    )

    if warnings:
        print("校验警告：")

        for warning in warnings:
            print(f"- {warning}")


def read_valid_attempt(
    *,
    workspace: Path,
    output_path: Path,
    run_result: CodexRunResult,
) -> ValidAttempt | None:
    """读取并完整校验一次Codex尝试。"""
    if (
        run_result.returncode != 0
        or run_result.timed_out
        or not output_path.exists()
    ):
        return None

    schema_errors = validate_schema_file(
        output_path=output_path,
        schema_path=(
            workspace
            / SCHEMA_RELATIVE_PATH
        ),
    )

    if schema_errors:
        print()
        print(
            "尝试输出未通过Schema："
            f"{output_path.name}"
        )

        for error in schema_errors:
            print(f"- {error}")

        return None

    validation = validate_portfolio_decision(
        workspace=workspace,
        output_path=output_path,
    )

    if not validation["valid"]:
        print()
        print(
            "尝试输出未通过Python业务校验："
            f"{output_path.name}"
        )

        for error in validation["errors"]:
            print(f"- {error}")

        if validation["warnings"]:
            print("校验警告：")

            for warning in (
                validation["warnings"]
            ):
                print(f"- {warning}")

        return None

    output = load_json_object(
        output_path
    )

    return ValidAttempt(
        output_path=output_path,
        output=output,
        validation=validation,
        run_result=run_result,
    )


def get_network_status(
    output: dict[str, Any],
) -> str:
    """读取联网研究状态。"""
    network = output.get(
        "network_research",
        {},
    )

    if not isinstance(network, dict):
        return "local_only"

    return (
        "success"
        if network.get("status")
        == "success"
        else "local_only"
    )


def validate_existing_output(
    *,
    workspace: Path,
) -> dict[str, Any] | None:
    """校验已有正式输出是否仍可复用。"""
    output_path = (
        workspace
        / OUTPUT_RELATIVE_PATH
    )

    if not output_path.exists():
        return None

    validation = validate_portfolio_decision(
        workspace=workspace,
        output_path=output_path,
    )

    if not validation["valid"]:
        print()
        print(
            "已有组合结果未通过当前校验，"
            "将重新调用Codex"
        )

        for error in validation["errors"]:
            print(f"- {error}")

        return None

    return validation


def install_validated_output(
    *,
    workspace: Path,
    valid_attempt: ValidAttempt,
) -> Path:
    """把已经完整验证的临时结果原子安装。"""
    output_path = (
        workspace
        / OUTPUT_RELATIVE_PATH
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    os.replace(
        valid_attempt.output_path,
        output_path,
    )

    return output_path


def cleanup_codex_temporary_files(
    workspace: Path,
) -> None:
    """清理第二阶段临时文件。"""
    temporary_directory = (
        workspace
        / ".tmp"
        / "codex"
    )

    if not temporary_directory.exists():
        return

    for path in (
        temporary_directory.iterdir()
    ):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(
                missing_ok=True
            )


def run_portfolio_decision(
    workspace: Path,
    *,
    force: bool,
    timeout_minutes: float,
    network_retry: bool,
) -> int:
    """完成第二次Codex组合决策调用。"""
    project_root = get_project_root()
    workspace = workspace.resolve()

    copy_runtime_contracts(
        project_root=project_root,
        workspace=workspace,
    )

    preflight_output_schema(
        workspace
        / SCHEMA_RELATIVE_PATH
    )

    print(
        "Codex输出Schema本地预检：通过"
    )

    input_hash = calculate_input_hash(
        workspace
    )

    state_path = get_state_path_for_workspace(
        workspace=workspace,
        project_root=project_root,
    )

    state = load_state(state_path)
    stages = state.get(
        "stages",
        {},
    )
    previous_stage = (
        stages.get(
            "portfolio_decision",
            {},
        )
        if isinstance(stages, dict)
        else {}
    )
    previous_hash = (
        previous_stage.get(
            "input_hash"
        )
        if isinstance(
            previous_stage,
            dict,
        )
        else None
    )

    existing_validation = None

    if (
        not force
        and previous_hash == input_hash
    ):
        existing_validation = (
            validate_existing_output(
                workspace=workspace
            )
        )

    if existing_validation is not None:
        print_validation_result(
            existing_validation,
            heading=(
                "已有第二阶段结果校验通过"
            ),
        )
        print()
        print(
            "组合决策输入与已验证结果"
            "完全相同，跳过重复Codex调用"
        )
        print(
            "已有阶段状态："
            f"{previous_stage.get('status')}"
        )

        return 0

    update_stage_state(
        state_path,
        input_hash=input_hash,
        status="running",
        attempt_count=0,
        message=(
            "第二阶段Codex组合决策正在运行"
        ),
    )

    temporary_directory = (
        workspace
        / ".tmp"
        / "codex"
    )
    temporary_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    first_output_path = (
        temporary_directory
        / "portfolio_decision.attempt1.json"
    )
    retry_output_path = (
        temporary_directory
        / "portfolio_decision.attempt2.json"
    )

    attempt_count = 0
    retry_strategy = "none"

    try:
        prompt = build_prompt(workspace)
        timeout_seconds = (
            timeout_minutes * 60
        )

        first_result = run_codex_attempt(
            workspace=workspace,
            prompt=prompt,
            temp_output_path=(
                first_output_path
            ),
            timeout_seconds=(
                timeout_seconds
            ),
            attempt_label=(
                "1/2：继承当前VPN和代理环境"
            ),
            clean_proxy_environment=False,
        )
        attempt_count = 1

        first_attempt = read_valid_attempt(
            workspace=workspace,
            output_path=first_output_path,
            run_result=first_result,
        )

        first_network_status = (
            get_network_status(
                first_attempt.output
            )
            if first_attempt is not None
            else "local_only"
        )

        network_failure = (
            first_result.timed_out
            or has_network_failure(
                first_result.stdout,
                first_result.stderr,
            )
            or first_network_status
            == "local_only"
        )

        chosen_attempt: (
            ValidAttempt | None
        ) = None

        if (
            first_attempt is not None
            and first_network_status
            == "success"
        ):
            chosen_attempt = first_attempt

        elif (
            network_retry
            and network_failure
        ):
            retry_strategy = (
                "temporary_clean_proxy_environment"
            )

            print()
            print(
                "检测到联网、搜索或传输异常，"
                "执行一次安全重试"
            )
            print(
                "只临时修改第二个Codex子进程环境，"
                "不会修改.env、VPN或当前终端"
            )

            retry_result = run_codex_attempt(
                workspace=workspace,
                prompt=prompt,
                temp_output_path=(
                    retry_output_path
                ),
                timeout_seconds=(
                    timeout_seconds
                ),
                attempt_label=(
                    "2/2：临时清理代理和"
                    "自定义证书环境"
                ),
                clean_proxy_environment=True,
            )
            attempt_count = 2

            retry_attempt = (
                read_valid_attempt(
                    workspace=workspace,
                    output_path=(
                        retry_output_path
                    ),
                    run_result=retry_result,
                )
            )

            if retry_attempt is not None:
                chosen_attempt = (
                    retry_attempt
                )

            elif first_attempt is not None:
                print()
                print(
                    "第二次尝试未得到合法结果，"
                    "回退使用第一次已验证的"
                    "local_only组合计划"
                )

                chosen_attempt = (
                    first_attempt
                )

            else:
                retry_network_failure = (
                    retry_result.timed_out
                    or has_network_failure(
                        retry_result.stdout,
                        retry_result.stderr,
                    )
                )

                raise RuntimeError(
                    (
                        "两次Codex调用均发生"
                        "网络或超时故障"
                    )
                    if retry_network_failure
                    else (
                        "两次Codex调用均未生成"
                        "通过校验的组合计划"
                    )
                )

        elif first_attempt is not None:
            chosen_attempt = first_attempt

        else:
            if first_result.timed_out:
                raise RuntimeError(
                    "第二阶段Codex调用超时"
                )

            raise RuntimeError(
                "第二阶段Codex没有生成"
                "通过Schema和业务校验的结果；"
                f"返回状态={first_result.returncode}"
            )

        if chosen_attempt is None:
            raise RuntimeError(
                "没有可安装的第二阶段结果"
            )

        output_path = (
            install_validated_output(
                workspace=workspace,
                valid_attempt=chosen_attempt,
            )
        )

        output = load_json_object(
            output_path
        )
        validation = (
            chosen_attempt.validation
        )

        output_hash = sha256_file(
            output_path
        )
        network_status = (
            get_network_status(output)
        )

        stage_status = (
            "success"
            if network_status == "success"
            else "success_local_only"
        )

        message = (
            "第二阶段组合决策完成，"
            "联网研究和Python校验均通过"
            if stage_status == "success"
            else (
                "第二阶段组合决策完成并通过"
                "Python校验，但实时联网研究失败；"
                "结果只能管理已有仓位和挂单，"
                "不得产生新开仓执行计划"
            )
        )

        update_stage_state(
            state_path,
            input_hash=input_hash,
            status=stage_status,
            output_hash=output_hash,
            network_research_status=(
                network_status
            ),
            position_decision_count=(
                validation.get(
                    "position_decision_count"
                )
            ),
            positive_target_count=(
                validation.get(
                    "positive_target_count"
                )
            ),
            target_position_count=(
                validation.get(
                    "target_position_count"
                )
            ),
            pending_order_review_count=(
                validation.get(
                    "pending_order_review_count"
                )
            ),
            attempt_count=attempt_count,
            retry_strategy=retry_strategy,
            message=message,
        )

        print_validation_result(
            validation,
            heading=(
                "第二阶段组合计划校验通过"
            ),
        )

        print()
        print("第二次Codex组合决策完成")
        print(f"阶段状态：{stage_status}")
        print(
            f"联网研究：{network_status}"
        )
        print(
            f"Codex调用次数：{attempt_count}"
        )
        print(f"输出：{output_path}")
        print(f"状态：{state_path}")

        if (
            stage_status
            == "success_local_only"
        ):
            print()
            print(
                "注意：本轮结果只能用于已有"
                "持仓、保护和挂单管理；"
                "第三阶段不得把任何零持仓标的"
                "转换为新开仓订单。"
            )

        return 0

    except Exception as error:
        update_stage_state(
            state_path,
            input_hash=input_hash,
            status="failed",
            attempt_count=attempt_count,
            retry_strategy=retry_strategy,
            message=str(error),
        )

        print()
        print("第二次Codex组合决策失败")
        print(f"错误信息：{error}")

        return 1

    finally:
        cleanup_codex_temporary_files(
            workspace
        )


def main() -> int:
    """命令行入口。"""
    print(
        f"脚本版本：{SCRIPT_VERSION}"
    )

    parser = argparse.ArgumentParser(
        description=(
            "自动调用Codex完成WA Trader v1"
            "第二阶段组合与仓位决策"
        )
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "组合决策工作区；默认使用纽约"
            "当天或最新工作区"
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "即使输入哈希相同也重新调用Codex"
        ),
    )

    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=(
            DEFAULT_CODEX_TIMEOUT_MINUTES
        ),
        help=(
            "每次Codex调用最大分钟数，"
            "默认45分钟"
        ),
    )

    parser.add_argument(
        "--no-network-retry",
        action="store_true",
        help=(
            "网络异常时不执行清理代理环境"
            "后的第二次安全尝试"
        ),
    )

    arguments = parser.parse_args()

    if arguments.timeout_minutes <= 0:
        parser.error(
            "--timeout-minutes必须大于0"
        )

    try:
        project_root = get_project_root()

        workspace = (
            arguments.workspace
            if arguments.workspace
            else find_latest_stage_workspace(
                "portfolio_decision",
                project_root=project_root,
            )
        )

        if not workspace.is_absolute():
            workspace = (
                project_root / workspace
            )

        return run_portfolio_decision(
            workspace=workspace,
            force=arguments.force,
            timeout_minutes=(
                arguments.timeout_minutes
            ),
            network_retry=(
                not arguments.no_network_retry
            ),
        )

    except Exception as error:
        print(
            "第二阶段Codex调用器启动失败"
        )
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
