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
from jsonschema import Draft202012Validator, FormatChecker

from runtime_paths import (
    build_runtime_paths,
    find_latest_stage_workspace,
    get_project_root,
)


SCRIPT_VERSION = "2026-07-22-atomic-contract-refresh-v6"


PROMPT_RELATIVE_PATH = Path(
    "prompts/coarse_selection.md"
)

SCHEMA_RELATIVE_PATH = Path(
    "schemas/coarse_candidates.schema.json"
)

OUTPUT_RELATIVE_PATH = Path(
    "output/coarse_candidates.json"
)

TEMP_OUTPUT_RELATIVE_PATH = Path(
    ".tmp/codex/coarse_candidates.new.json"
)


DEFAULT_CODEX_TIMEOUT_MINUTES = 45

NETWORK_ERROR_PATTERNS = (
    "transport channel closed",
    "request timed out",
    "network error",
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


def load_json_object(path: Path) -> dict[str, Any]:
    """读取JSON对象。"""
    if not path.exists():
        raise FileNotFoundError(f"缺少文件：{path}")

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(f"JSON顶层必须是对象：{path}")

    return payload


def save_json_atomically(
    path: Path,
    payload: dict[str, Any],
) -> None:
    """原子更新JSON文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)

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

    temporary_path.replace(path)


def replace_contract_atomically(
    source: Path,
    destination: Path,
) -> None:
    """
    原子刷新工作区运行契约。

    工作区中的旧Prompt和Schema可能被设为只读，
    因此不能直接使用shutil.copy2覆盖。先复制到同目录
    临时文件，再通过os.replace替换目录项。
    """
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        destination.parent
        / f".{destination.name}.runtime_refresh.tmp"
    )

    if temporary_path.exists():
        temporary_path.unlink()

    try:
        shutil.copy2(
            source,
            temporary_path,
        )

        # 临时文件必须可被当前进程替换和清理。
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

        # 安装完成后恢复工作区输入只读属性。
        try:
            os.chmod(
                destination,
                0o444,
            )
        except OSError:
            pass

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def copy_runtime_contracts(
    project_root: Path,
    workspace: Path,
) -> None:
    """
    把最新提示词和Schema原子刷新到工作区。

    不直接覆盖只读文件，也不触碰output、research或状态文件。
    """
    mappings = [
        (
            project_root / PROMPT_RELATIVE_PATH,
            workspace / PROMPT_RELATIVE_PATH,
        ),
        (
            project_root / SCHEMA_RELATIVE_PATH,
            workspace / SCHEMA_RELATIVE_PATH,
        ),
    ]

    for source, destination in mappings:
        if not source.exists():
            raise FileNotFoundError(
                f"项目中缺少运行契约：{source}"
            )

        replace_contract_atomically(
            source=source,
            destination=destination,
        )


def strip_volatile_values(value: Any) -> Any:
    """
    删除不影响决策内容的运行时间字段。

    避免只是重新生成快照时间就被误判为新输入。
    """
    volatile_keys = {
        "generated_at",
        "validated_at",
        "downloaded_at",
        "saved_at",
    }

    if isinstance(value, dict):
        return {
            key: strip_volatile_values(item)
            for key, item in sorted(value.items())
            if key not in volatile_keys
        }

    if isinstance(value, list):
        return [
            strip_volatile_values(item)
            for item in value
        ]

    return value


def update_hash_with_json(
    digest: Any,
    path: Path,
    relative_name: str,
) -> None:
    """将规范化JSON加入输入哈希。"""
    payload = load_json_object(path)
    normalized = strip_volatile_values(payload)

    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest.update(relative_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(encoded)
    digest.update(b"\0")


def calculate_input_hash(
    workspace: Path,
) -> str:
    """
    计算第一次Codex调用的实质输入哈希。

    包含：
    - 粗选输入；
    - 账户、持仓、挂单和当日订单；
    - 所有允许研究标的的长日线；
    - 粗选提示词和Schema。
    """
    digest = hashlib.sha256()

    required_json_files = [
        Path("data/snapshots/coarse_universe_input.json"),
        Path("data/snapshots/account.json"),
        Path("data/snapshots/positions.json"),
        Path("data/snapshots/open_orders.json"),
    ]

    optional_json_files = [
        Path("data/snapshots/today_orders.json"),
    ]

    for relative_path in required_json_files:
        path = workspace / relative_path

        if not path.exists():
            raise FileNotFoundError(
                f"粗选输入缺失：{path}"
            )

        update_hash_with_json(
            digest,
            path,
            relative_path.as_posix(),
        )

    for relative_path in optional_json_files:
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

    if not daily_files:
        raise FileNotFoundError(
            "粗选工作区没有日线文件"
        )

    for path in daily_files:
        relative_name = str(
            path.relative_to(workspace)
        )

        update_hash_with_json(
            digest,
            path,
            relative_name,
        )

    for relative_path in (
        PROMPT_RELATIVE_PATH,
        SCHEMA_RELATIVE_PATH,
    ):
        path = workspace / relative_path
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    return digest.hexdigest()


def get_state_path_for_workspace(
    workspace: Path,
    project_root: Path,
) -> Path:
    """
    根据规范工作区日期返回唯一decision_state.json路径。

    明确指定旧日期工作区时，状态仍写回该日期目录，
    不会误写到纽约当天。
    """
    run_date = workspace.parent.name

    paths = build_runtime_paths(
        run_date,
        project_root=project_root,
    )

    expected_workspace = (
        paths.coarse_workspace.resolve()
    )

    if workspace.resolve() != expected_workspace:
        raise ValueError(
            "粗选工作区不符合统一路径规范："
            f"实际={workspace.resolve()}；"
            f"规范={expected_workspace}"
        )

    return paths.decision_state


def load_state(state_path: Path) -> dict[str, Any]:
    """读取当天统一运行状态。"""
    if not state_path.exists():
        return {
            "schema_version": "1.0",
            "run_date": state_path.parent.name,
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
    selection_count: int | None = None,
    message: str | None = None,
    network_research_status: str | None = None,
    attempt_count: int | None = None,
    retry_strategy: str | None = None,
) -> None:
    """更新当天唯一decision_state.json。"""
    state = load_state(state_path)

    stages = state.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        state["stages"] = stages

    stages["coarse_selection"] = {
        "status": status,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "selection_count": selection_count,
        "network_research_status": (
            network_research_status
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

    save_json_atomically(state_path, state)


def sha256_file(path: Path) -> str:
    """计算文件SHA-256。"""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def validate_schema_file(
    output_path: Path,
    schema_path: Path,
) -> list[str]:
    """在替换正式输出前执行Schema预校验。"""
    output = load_json_object(output_path)
    schema = load_json_object(schema_path)

    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(output),
        key=lambda item: (
            list(item.absolute_path),
            item.message,
        ),
    )

    messages: list[str] = []

    for error in errors:
        path = ".".join(
            str(item)
            for item in error.absolute_path
        )

        messages.append(
            f"{path or '$'}：{error.message}"
        )

    return messages


def run_business_validator(
    project_root: Path,
    workspace: Path,
) -> subprocess.CompletedProcess[str]:
    """运行外部粗选业务校验器。"""
    validator_path = (
        project_root
        / "src"
        / "v1"
        / "validate_coarse_candidates.py"
    )

    if not validator_path.exists():
        raise FileNotFoundError(
            f"缺少校验器：{validator_path}"
        )

    return subprocess.run(
        [
            sys.executable,
            "-u",
            str(validator_path),
            "--workspace",
            str(workspace),
        ],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def validate_existing_output(
    project_root: Path,
    workspace: Path,
) -> bool:
    """检查已有正式输出能否继续复用。"""
    output_path = workspace / OUTPUT_RELATIVE_PATH

    if not output_path.exists():
        return False

    completed = run_business_validator(
        project_root=project_root,
        workspace=workspace,
    )

    if completed.stdout:
        print(completed.stdout, end="")

    if completed.stderr:
        print(
            completed.stderr,
            end="",
            file=sys.stderr,
        )

    return completed.returncode == 0


def build_prompt(
    workspace: Path,
) -> str:
    """构建第一次Codex的最终指令。"""
    prompt_path = workspace / PROMPT_RELATIVE_PATH
    base_prompt = prompt_path.read_text(encoding="utf-8")

    launcher_instruction = """
## 自动调用器补充约束

本次由 Python 非交互调用器启动。

- 不要通过 shell 直接创建或覆盖 `output/coarse_candidates.json`。
- 你的最终回复本身必须是严格符合
  `schemas/coarse_candidates.schema.json` 的单个 JSON 对象。
- Python 调用器会把最终回复先写入临时文件，校验通过后再原子更新
  当天唯一的 `output/coarse_candidates.json`。
- 可以写入研究文件和临时辅助函数，但不得再创建其他候选 JSON。
- 不要在最终 JSON 前后添加 Markdown、代码围栏或解释文字。
"""

    return (
        base_prompt.rstrip()
        + "\n\n"
        + launcher_instruction.strip()
        + "\n"
    )



def validate_openai_output_schema(
    schema: dict[str, Any],
) -> list[str]:
    """
    对Codex严格结构化输出Schema做本地预检。

    这里只检查本项目需要的关键限制：
    - 每个实际值Schema必须显式包含type或$ref；
    - 每个对象都设置additionalProperties=false；
    - 对象properties中的字段全部列入required；
    - 不使用已知不支持的关键字。
    """
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
    }

    def walk(
        node: Any,
        path: str,
    ) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
            return

        if not isinstance(node, dict):
            return

        for keyword in unsupported_keywords:
            if keyword in node:
                errors.append(
                    f"{path}使用不支持的关键字：{keyword}"
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
                        f"{path}存在未列入required的字段："
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
    """调用Codex前先本地拒绝不兼容Schema。"""
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


@dataclass
class CodexRunResult:
    """一次Codex子进程调用结果。"""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    attempt_label: str


def has_network_failure(
    *texts: str,
) -> bool:
    """根据Codex日志识别传输、搜索或代理类故障。"""
    combined = "\n".join(
        text.lower()
        for text in texts
        if text
    )

    return any(
        pattern in combined
        for pattern in NETWORK_ERROR_PATTERNS
    )


def build_child_environment(
    *,
    clean_proxy_environment: bool,
) -> tuple[dict[str, str], list[str]]:
    """
    构建Codex子进程环境。

    第二次尝试只在子进程中临时移除代理和自定义证书变量，
    不修改当前终端、系统VPN或项目.env。
    """
    environment = os.environ.copy()

    present_names = [
        name
        for name in PROXY_ENVIRONMENT_NAMES
        if environment.get(name)
    ]

    if clean_proxy_environment:
        for name in PROXY_ENVIRONMENT_NAMES:
            environment.pop(name, None)

    return environment, present_names


def stream_pipe(
    stream: Any,
    target: Any,
    collector: list[str],
) -> None:
    """实时转发子进程输出并保留内存副本。"""
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
    workspace: Path,
    temp_output_path: Path,
) -> list[str]:
    """构建非交互Codex CLI命令。"""
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
    """执行一次可超时、可实时显示日志的Codex调用。"""
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

    environment, present_proxy_names = (
        build_child_environment(
            clean_proxy_environment=(
                clean_proxy_environment
            )
        )
    )

    print()
    print(f"Codex调用尝试：{attempt_label}")
    print(f"工作区：{workspace}")
    print("网络：启用Codex实时Web搜索")
    print("文件权限：仅允许写当前粗选工作区")
    print(
        "检测到的代理/证书环境变量名称："
        + (
            ", ".join(present_proxy_names)
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
            "无法建立Codex子进程输入输出管道"
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
            "正在终止子进程"
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


def read_valid_attempt_output(
    *,
    output_path: Path,
    schema_path: Path,
) -> dict[str, Any] | None:
    """读取通过Schema预校验的尝试输出。"""
    if not output_path.exists():
        return None

    schema_errors = validate_schema_file(
        output_path=output_path,
        schema_path=schema_path,
    )

    if schema_errors:
        print()
        print(
            f"尝试输出未通过Schema：{output_path.name}"
        )
        for error in schema_errors:
            print(f"- {error}")
        return None

    return load_json_object(output_path)


def get_network_research_status(
    output: dict[str, Any],
) -> str:
    """读取模型明确声明的联网研究状态。"""
    network_research = output.get(
        "network_research",
        {},
    )

    if not isinstance(
        network_research,
        dict,
    ):
        return "local_only"

    status = network_research.get("status")

    if status == "success":
        return "success"

    return "local_only"


def cleanup_codex_temporary_files(
    workspace: Path,
) -> None:
    """删除一次Codex调用产生的临时文件。"""
    temporary_directory = (
        workspace
        / ".tmp"
        / "codex"
    )

    if not temporary_directory.exists():
        return

    for path in temporary_directory.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def install_validated_output(
    *,
    project_root: Path,
    workspace: Path,
    temp_output_path: Path,
) -> None:
    """
    Schema预校验后原子安装，并执行完整业务校验。

    如果业务校验失败，恢复此前的有效输出。
    """
    output_path = workspace / OUTPUT_RELATIVE_PATH
    schema_path = workspace / SCHEMA_RELATIVE_PATH

    schema_errors = validate_schema_file(
        output_path=temp_output_path,
        schema_path=schema_path,
    )

    if schema_errors:
        raise RuntimeError(
            "Codex粗选输出未通过Schema预校验：\n- "
            + "\n- ".join(schema_errors)
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    previous_bytes = (
        output_path.read_bytes()
        if output_path.exists()
        else None
    )

    os.replace(
        temp_output_path,
        output_path,
    )

    completed = run_business_validator(
        project_root=project_root,
        workspace=workspace,
    )

    if completed.stdout:
        print(completed.stdout, end="")

    if completed.stderr:
        print(
            completed.stderr,
            end="",
            file=sys.stderr,
        )

    if completed.returncode == 0:
        return

    if previous_bytes is None:
        output_path.unlink(missing_ok=True)
    else:
        rollback_path = output_path.with_suffix(
            output_path.suffix + ".rollback"
        )
        rollback_path.write_bytes(previous_bytes)
        os.replace(rollback_path, output_path)

    raise RuntimeError(
        "Codex粗选输出未通过Python业务校验，"
        "已恢复此前输出"
    )



def run_coarse_selection(
    workspace: Path,
    *,
    force: bool,
    timeout_minutes: float,
    network_retry: bool,
) -> int:
    """完成第一次Codex调用、联网降级和业务校验。"""
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

    state_path = (
        get_state_path_for_workspace(
            workspace=workspace,
            project_root=project_root,
        )
    )

    state = load_state(state_path)
    stages = state.get("stages", {})
    previous_stage = (
        stages.get(
            "coarse_selection",
            {},
        )
        if isinstance(stages, dict)
        else {}
    )

    previous_hash = (
        previous_stage.get("input_hash")
        if isinstance(previous_stage, dict)
        else None
    )

    if (
        not force
        and previous_hash == input_hash
        and validate_existing_output(
            project_root=project_root,
            workspace=workspace,
        )
    ):
        previous_status = (
            previous_stage.get("status")
            if isinstance(
                previous_stage,
                dict,
            )
            else None
        )

        print()
        print(
            "粗选输入与已验证结果完全相同，"
            "跳过重复Codex调用"
        )
        print(
            f"已有阶段状态：{previous_status}"
        )
        return 0

    update_stage_state(
        state_path,
        input_hash=input_hash,
        status="running",
        message="第一次Codex粗选正在运行",
        attempt_count=0,
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
        / "coarse_candidates.attempt1.json"
    )
    retry_output_path = (
        temporary_directory
        / "coarse_candidates.attempt2.json"
    )
    canonical_temp_output_path = (
        workspace
        / TEMP_OUTPUT_RELATIVE_PATH
    )

    attempt_count = 0
    retry_strategy = "none"

    try:
        prompt = build_prompt(workspace)
        timeout_seconds = (
            timeout_minutes * 60
        )
        schema_path = (
            workspace
            / SCHEMA_RELATIVE_PATH
        )

        first_result = run_codex_attempt(
            workspace=workspace,
            prompt=prompt,
            temp_output_path=first_output_path,
            timeout_seconds=timeout_seconds,
            attempt_label=(
                "1/2：继承当前VPN和代理环境"
            ),
            clean_proxy_environment=False,
        )
        attempt_count = 1

        first_output = (
            read_valid_attempt_output(
                output_path=first_output_path,
                schema_path=schema_path,
            )
            if first_result.returncode == 0
            and not first_result.timed_out
            else None
        )

        first_network_status = (
            get_network_research_status(
                first_output
            )
            if first_output is not None
            else "local_only"
        )

        first_network_failure = (
            first_result.timed_out
            or has_network_failure(
                first_result.stdout,
                first_result.stderr,
            )
            or first_network_status
            == "local_only"
        )

        chosen_output_path: Path | None = None
        chosen_output: dict[str, Any] | None = None

        if (
            first_output is not None
            and first_network_status
            == "success"
        ):
            chosen_output_path = (
                first_output_path
            )
            chosen_output = first_output

        elif (
            network_retry
            and first_network_failure
        ):
            retry_strategy = (
                "temporary_clean_proxy_environment"
            )

            print()
            print(
                "检测到联网或传输异常，"
                "执行一次安全重试"
            )
            print(
                "重试只影响新的Codex子进程，"
                "不会修改.env、VPN或终端环境"
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

            retry_output = (
                read_valid_attempt_output(
                    output_path=(
                        retry_output_path
                    ),
                    schema_path=schema_path,
                )
                if retry_result.returncode
                == 0
                and not retry_result.timed_out
                else None
            )

            if retry_output is not None:
                chosen_output_path = (
                    retry_output_path
                )
                chosen_output = retry_output
            elif first_output is not None:
                print()
                print(
                    "网络重试未得到有效输出，"
                    "回退使用第一次本地粗选结果"
                )
                chosen_output_path = (
                    first_output_path
                )
                chosen_output = first_output
            else:
                retry_network_failure = (
                    retry_result.timed_out
                    or has_network_failure(
                        retry_result.stdout,
                        retry_result.stderr,
                    )
                )

                reason = (
                    "两次Codex调用均发生"
                    "网络/超时故障"
                    if retry_network_failure
                    else (
                        "网络重试未生成"
                        "合法结构化输出"
                    )
                )

                raise RuntimeError(reason)

        elif first_output is not None:
            chosen_output_path = (
                first_output_path
            )
            chosen_output = first_output

        else:
            if first_result.timed_out:
                raise RuntimeError(
                    "Codex粗选调用超时"
                )

            raise RuntimeError(
                "Codex没有生成合法粗选输出；"
                f"返回状态={first_result.returncode}"
            )

        if (
            chosen_output_path is None
            or chosen_output is None
        ):
            raise RuntimeError(
                "没有可安装的粗选结果"
            )

        shutil.copy2(
            chosen_output_path,
            canonical_temp_output_path,
        )

        install_validated_output(
            project_root=project_root,
            workspace=workspace,
            temp_output_path=(
                canonical_temp_output_path
            ),
        )

        output_path = (
            workspace
            / OUTPUT_RELATIVE_PATH
        )
        output = load_json_object(
            output_path
        )

        output_hash = sha256_file(
            output_path
        )
        selection_count = len(
            output.get("selected", [])
        )
        network_research_status = (
            get_network_research_status(
                output
            )
        )

        stage_status = (
            "success"
            if network_research_status
            == "success"
            else "success_local_only"
        )

        message = (
            "第一次Codex粗选完成，"
            "联网研究和Python校验均通过"
            if stage_status == "success"
            else (
                "第一次Codex粗选完成并通过"
                "Python校验，但实时联网研究失败；"
                "第二阶段必须重新尝试联网"
            )
        )

        update_stage_state(
            state_path,
            input_hash=input_hash,
            status=stage_status,
            output_hash=output_hash,
            selection_count=selection_count,
            message=message,
            network_research_status=(
                network_research_status
            ),
            attempt_count=attempt_count,
            retry_strategy=retry_strategy,
        )

        print()
        print("第一次Codex粗选完成")
        print(f"候选数量：{selection_count}")
        print(f"阶段状态：{stage_status}")
        print(
            "联网研究："
            f"{network_research_status}"
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
                "注意：本轮可作为本地技术面"
                "粗选结果继续处理，"
                "但第二阶段必须重新联网；"
                "若仍失败，不应生成新开仓计划。"
            )

        return 0

    except Exception as error:
        update_stage_state(
            state_path,
            input_hash=input_hash,
            status="failed",
            message=str(error),
            attempt_count=attempt_count,
            retry_strategy=retry_strategy,
        )

        print()
        print("第一次Codex粗选失败")
        print(f"错误信息：{error}")

        return 1

    finally:
        cleanup_codex_temporary_files(
            workspace
        )


def main() -> int:
    """命令行入口。"""
    print(f"脚本版本：{SCRIPT_VERSION}")

    parser = argparse.ArgumentParser(
        description=(
            "自动调用Codex完成WA Trader v1"
            "第一次全市场粗选"
        )
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "粗选工作区；默认使用纽约当天"
            "或最新工作区"
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "即使输入哈希相同，也重新调用Codex"
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
            "检测到网络故障时不执行"
            "清理代理环境后的第二次尝试"
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
                "coarse_selection",
                project_root=project_root,
            )
        )

        if not workspace.is_absolute():
            workspace = (
                project_root / workspace
            )

        return run_coarse_selection(
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
        print("粗选Codex调用器启动失败")
        print(f"错误信息：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())