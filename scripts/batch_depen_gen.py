#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.cache_utils import sanitize_model_name

PROJECT_JSON = ROOT / "project.json"
RUN_SCAN = ROOT / "run_scan.py"
TEMPLATE_CACHE = ROOT / "cache_template"

DEFAULT_TARGET_APPS = [
    "JeecgBoot",
    "mall",
    "youlai-mall",
    "newbee-mall-plus",
    "mall-swarm",
    "newbee_mall",
    "pybbs",
    "TIME-SEA-chatgpt",
    "ctfd",
    "gin-vue-admin",
    "openemr",
    "gin-vue-blog",
    "crapi",
    "easyappointments",
]

ABLATION_RUNS = [
    "no-group",
    "no-param-mapping",
    "static-api-type",
    "static-identifier",
]

RUN_SCAN_ABLATION_FLAGS = {
    "no-group": "--ablation-no-group",
    "no-param-mapping": "--ablation-no-param-mapping",
    "static-api-type": "--ablation-static-api-type",
    "static-identifier": "--static-identifier-only",
}

MODEL_ENDPOINT_ADAPTERS = {
    "claude-haiku-4-5-20251001": {
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gemini-2.5-flash-preview-09-2025": {
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gpt-4o-mini": {
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gpt-5-mini": {
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "deepseek-chat": {
        "base_url": "https://api.deepseek.com/v1",
        "endpoint_mode": "deepseek_chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen-plus": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
    "qwen-max": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
    "qwen-turbo": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
    "qwen3.6-flash-2026-04-16": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
}


def normalize(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def load_projects() -> dict:
    raw = json.loads(PROJECT_JSON.read_text(encoding="utf-8"))
    projects = raw.get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError("project.json 中的 projects 格式不正确")
    return projects


def resolve_project_key(target_name: str, projects: dict) -> str | None:
    target_norm = normalize(target_name)
    normalized_lookup = {}
    for key, value in projects.items():
        normalized_lookup[normalize(key)] = key
        if isinstance(value, dict):
            project_name = value.get("project_name")
            if isinstance(project_name, str) and project_name.strip():
                normalized_lookup[normalize(project_name.strip())] = key
    return normalized_lookup.get(target_norm)


def parse_csv_arg(raw: str | None) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalize_model_name(model: str) -> str:
    return model.strip().lower()


def build_model_env(model: str) -> dict[str, str]:
    env = os.environ.copy()
    model_key = normalize_model_name(model)
    adapter = MODEL_ENDPOINT_ADAPTERS.get(model_key)
    if not adapter and (model_key.startswith("qwen") or model_key.startswith("qw")):
        adapter = {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "endpoint_mode": "responses",
            "api_key_env": "DASHSCOPE_API_KEY",
            "timeout_s": "180",
            "max_workers": "1",
            "max_retries": "2",
        }
    if not adapter:
        env["BOLASCAN_LLM_MODEL"] = model
        return env

    env["BOLASCAN_LLM_MODEL"] = model
    env["BOLASCAN_LLM_BASE_URL"] = adapter["base_url"]
    env["BOLASCAN_LLM_ENDPOINT_MODE"] = adapter["endpoint_mode"]
    if adapter.get("timeout_s") and not env.get("BOLASCAN_LLM_TIMEOUT"):
        env["BOLASCAN_LLM_TIMEOUT"] = adapter["timeout_s"]
    if adapter.get("max_workers") and not env.get("BOLASCAN_LLM_MAX_WORKERS"):
        env["BOLASCAN_LLM_MAX_WORKERS"] = adapter["max_workers"]
    if adapter.get("max_retries") and not env.get("BOLASCAN_LLM_MAX_RETRIES"):
        env["BOLASCAN_LLM_MAX_RETRIES"] = adapter["max_retries"]

    api_key = env.get(adapter["api_key_env"], "").strip()
    if api_key:
        env["BOLASCAN_LLM_API_KEY"] = api_key
    return env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量执行 dependency generation 实验，并按模型隔离输出到 cache_{model}/<project>"
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="要执行的模型，可重复传入；也支持逗号分隔，例如 --model gpt-4o-mini --model deepseek",
    )
    parser.add_argument(
        "--projects",
        help="仅执行指定项目，逗号分隔；默认执行脚本内置项目列表",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="执行 project.json 中的全部项目，而不是默认内置列表",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="某个项目失败后继续跑剩余任务",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="运行前清理目标项目的生成产物，只保留 OpenAPI 与 http-requests 等种子文件，确保本轮重新生成结果",
    )
    parser.add_argument(
        "--parallel-projects",
        type=int,
        default=1,
        help="项目级并发数，默认 1；例如 --parallel-projects 4 表示同时跑 4 个项目",
    )
    parser.add_argument(
        "--resume-missing-container-divide",
        action="store_true",
        help="只检查并补生成缺失的 container_resource_divide_results.json；已存在则跳过",
    )
    parser.add_argument(
        "--horizontal-test-only",
        action="store_true",
        help="复用已识别的 identifier/container divide 结果，只执行水平 BOLA 测试阶段",
    )
    parser.add_argument(
        "--horizontal-judgement-only",
        action="store_true",
        help="复用已有 all_acount_execution_results.json，只重新执行最终 BOLA 语义校验/判定阶段",
    )
    parser.add_argument(
        "--force-container-divide",
        action="store_true",
        help="强制重新生成 container divide 结果，即使目标文件已存在也不跳过",
    )
    parser.add_argument(
        "--post-depen-gen",
        action="store_true",
        help="续跑模式：不重建 cache_template，不再执行 depen-gen，只基于已有缓存继续补后续产物",
    )
    parser.add_argument(
        "--skip-dependency-chain",
        action="store_true",
        help="跳过依赖序列生成（Step 3）；优先复用已有 dependency_chains_results.json",
    )
    parser.add_argument(
        "--api-doc-with-type-only",
        action="store_true",
        help="只重新生成 api_doc_with_type.json，生成后立即停止",
    )
    parser.add_argument(
        "--parameter-mapping-only",
        action="store_true",
        help="只重新生成 parameters_dict_all.json，生成后立即停止",
    )
    parser.add_argument(
        "--dependency-chain-only",
        action="store_true",
        help="只重新生成 dependency_chains_results.json，要求前置的 api_doc_with_type.json 和 parameters_dict_all.json 已存在",
    )
    parser.add_argument(
        "--ablation",
        action="append",
        metavar="{all,no-group,no-param-mapping,static-api-type,static-identifier}",
        help=(
            "执行消融实验；可重复传入。all 会依次生成 no-group、no-param-mapping、"
            "static-api-type、static-identifier 四组结果"
        ),
    )
    parser.add_argument(
        "--refine-max-depth",
        type=int,
        help="递归细分功能组的最大深度；默认不指定/不限制，传正整数表示限制深度",
    )
    return parser.parse_args()


def resolve_models(args: argparse.Namespace) -> list[str]:
    models: list[str] = []
    for item in args.models or []:
        models.extend(parse_csv_arg(item))
    if not models:
        models = ["gpt-4o-mini"]
    return models


def resolve_targets(args: argparse.Namespace, projects: dict) -> list[str]:
    if args.all_projects:
        return list(projects.keys())
    requested = parse_csv_arg(args.projects)
    return requested or list(DEFAULT_TARGET_APPS)


def resolve_ablation_runs(args: argparse.Namespace) -> list[str | None]:
    requested: list[str] = []
    for item in args.ablation or []:
        requested.extend(parse_csv_arg(item))
    if not requested:
        return [None]
    if "all" in requested:
        return list(ABLATION_RUNS)
    deduped: list[str] = []
    for item in requested:
        if item not in ABLATION_RUNS:
            raise ValueError(f"未知 ablation 模式: {item}")
        if item not in deduped:
            deduped.append(item)
    return deduped


def cache_root_for_run(model: str, ablation: str | None) -> Path:
    model_part = sanitize_model_name(model)
    if not ablation:
        return ROOT / f"cache_{model_part}"
    return ROOT / f"cache_{model_part}_{ablation.replace('-', '_')}"


def is_openapi_json(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith("openapi.json")


def is_seed_cache_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith("openapi.json")
        or name == "openapi_formated.json"
        or name == "http-requests.json"
    )


def is_skip_dependency_chain_seed_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        is_seed_cache_file(path)
        or name == "create_request_data_packages_results.json"
    )


GENERATED_CACHE_FILES = {
    "api_doc_with_type.json",
    "parameters_dict_all.json",
    "dependency_chains_results.json",
    "case_hadling_from_click_data_resutls.json",
    "dependency_chain_with_parameters_results.json",
    "add_type_api_packages_results.json",
    "add_parameters_from_click_data.json",
    "create_request_data_packages_results.json",
    "bola_horizontal_results.json",
    "dependency_execution_reoutes_packages.json",
    "all_acount_execution_results.json",
    "batch_depen_gen.log",
    "llm_usage.json",
    "llm_call_audit.jsonl",
}


def clear_generated_outputs(cache_root: Path, project_key: str) -> None:
    project_dir = cache_root / project_key
    if not project_dir.exists():
        return

    for name in GENERATED_CACHE_FILES:
        path = project_dir / name
        if path.exists() and path.is_file():
            path.unlink()

    horizontal_results = project_dir / "horizontal_results"
    if horizontal_results.exists() and horizontal_results.is_dir():
        shutil.rmtree(horizontal_results)


def container_divide_outputs(cache_root: Path, project_key: str) -> list[Path]:
    horizontal_results = cache_root / project_key / "horizontal_results"
    return [
        horizontal_results / "data_resource_id_result.json",
        horizontal_results / "container_reoust_id_result.json",
        horizontal_results / "container_resource_divide_results.json",
    ]


def has_container_divide_outputs(cache_root: Path, project_key: str) -> bool:
    return all(path.exists() for path in container_divide_outputs(cache_root, project_key))


def prepare_model_cache_from_template(cache_root: Path, skip_dependency_chain: bool = False) -> None:
    """
    Seed cache_<model> with OpenAPI documents from cache_template without
    deleting any existing project JSON outputs.
    """
    if not TEMPLATE_CACHE.exists() or not TEMPLATE_CACHE.is_dir():
        cache_root.mkdir(parents=True, exist_ok=True)
        return

    cache_root.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE_CACHE.rglob("*"):
        if not src.is_file():
            continue
        if skip_dependency_chain:
            should_copy = is_skip_dependency_chain_seed_file(src)
        else:
            should_copy = is_seed_cache_file(src)
        if not should_copy:
            continue
        relative = src.relative_to(TEMPLATE_CACHE)
        dst = cache_root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def run_target_command(
    target: str,
    project_key: str,
    model: str,
    cache_root: Path,
    args: argparse.Namespace,
    ablation: str | None = None,
) -> tuple[str, str, object]:
    def run_with_project_log(command: list[str]) -> subprocess.CompletedProcess:
        project_cache_dir = cache_root / project_key
        project_cache_dir.mkdir(parents=True, exist_ok=True)
        log_path = project_cache_dir / "batch_depen_gen.log"
        env = build_model_env(model)
        horizontal_results_dir = project_cache_dir / "horizontal_results"
        horizontal_results_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault("BOLASCAN_LLM_AUDIT", "1")
        env["BOLASCAN_PROJECT_NAME"] = project_key
        env["BOLASCAN_RUN_MODE"] = (
            "ablation" if ablation else
            "api-doc-with-type-only" if args.api_doc_with_type_only else
            "parameter-mapping-only" if args.parameter_mapping_only else
            "dependency-chain-only" if args.dependency_chain_only else
            "horizontal-test-only" if args.horizontal_test_only else
            "horizontal-judgement-only" if args.horizontal_judgement_only else
            "container-divide-only" if (args.resume_missing_container_divide or args.post_depen_gen) else
            "depen-gen-with-container-divide"
        )
        env["BOLASCAN_ABLATION"] = ablation or ""
        env["BOLASCAN_LLM_AUDIT_PATH"] = str(horizontal_results_dir / "llm_call_audit.jsonl")
        started_at = datetime.now().isoformat(timespec="seconds")

        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write("\n" + "=" * 80 + "\n")
            log_file.write(f"[start] {started_at}\n")
            log_file.write(f"[target] {target}\n")
            log_file.write(f"[project] {project_key}\n")
            log_file.write(f"[model] {model}\n")
            if ablation:
                log_file.write(f"[ablation] {ablation}\n")
            log_file.write("$ " + shlex.join(command) + "\n\n")
            log_file.flush()

            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log_file.write(line)
            returncode = process.wait()
            finished_at = datetime.now().isoformat(timespec="seconds")
            log_file.write(f"\n[finish] {finished_at}\n")
            log_file.write(f"[exit] {returncode}\n")

        return subprocess.CompletedProcess(command, returncode)

    if args.api_doc_with_type_only:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--api-doc-with-type-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.refine_max_depth is not None:
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[api-doc-with-type-only] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if ablation:
        flag = RUN_SCAN_ABLATION_FLAGS[ablation]
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            flag,
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.refine_max_depth is not None and ablation != "static-identifier":
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[ablation:{ablation}] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if args.parameter_mapping_only:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--parameter-mapping-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.refine_max_depth is not None:
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[parameter-mapping-only] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if args.dependency_chain_only:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--dependency-chain-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.refine_max_depth is not None:
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[dependency-chain-only] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if args.horizontal_test_only:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--horizontal-test-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        print(f"[horizontal-test-only] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if args.horizontal_judgement_only:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--horizontal-judgement-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        print(f"[horizontal-judgement-only] {target} -> project={project_key} model={model}")
        result = run_with_project_log(command)
        if result.returncode == 0:
            print(f"[ok] {target}")
            return ("success", target, None)
        print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
        return ("failed", target, result.returncode)

    if (args.resume_missing_container_divide or args.post_depen_gen) and not args.force_container_divide and has_container_divide_outputs(cache_root, project_key):
        print(f"[skip] {target}: container-divide 三个结果文件都已存在")
        return ("skipped", target, "container-divide-exists")

    if args.resume_missing_container_divide or args.post_depen_gen:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--container-divide-only",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.force_container_divide:
            command.append("--force-container-divide")
        if args.skip_dependency_chain:
            command.append("--skip-dependency-chain")
        if args.refine_max_depth is not None:
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[continue] {target} -> project={project_key} model={model}")
    else:
        command = [
            sys.executable,
            str(RUN_SCAN),
            "--project",
            project_key,
            "--depen-gen",
            "--with-container-divide",
            "--model",
            model,
            "--cache-root",
            str(cache_root),
        ]
        if args.skip_dependency_chain:
            command.append("--skip-dependency-chain")
        if args.refine_max_depth is not None:
            command.extend(["--refine-max-depth", str(args.refine_max_depth)])
        print(f"[run] {target} -> project={project_key} model={model}")

    result = run_with_project_log(command)
    if result.returncode == 0:
        print(f"[ok] {target}")
        return ("success", target, None)

    print(f"[fail] {target}: exit={result.returncode}; log={cache_root / project_key / 'batch_depen_gen.log'}")
    return ("failed", target, result.returncode)


def print_progress(completed: int, total: int, summary: dict[str, list]) -> None:
    remaining = max(total - completed, 0)
    print(
        "[progress] "
        f"{completed}/{total} done, "
        f"remaining={remaining}, "
        f"success={len(summary['success'])}, "
        f"failed={len(summary['failed'])}, "
        f"skipped={len(summary['skipped'])}"
    )


def main() -> int:
    args = parse_args()
    if args.post_depen_gen and args.resume_missing_container_divide:
        raise ValueError("--post-depen-gen 和 --resume-missing-container-divide 不能同时使用")
    if args.parameter_mapping_only and (args.post_depen_gen or args.resume_missing_container_divide or args.api_doc_with_type_only or args.dependency_chain_only or args.horizontal_test_only or args.horizontal_judgement_only):
        raise ValueError("--parameter-mapping-only 不能与 --post-depen-gen、--resume-missing-container-divide 或 --api-doc-with-type-only 同时使用")
    if args.dependency_chain_only and (args.post_depen_gen or args.resume_missing_container_divide or args.api_doc_with_type_only or args.parameter_mapping_only or args.horizontal_test_only or args.horizontal_judgement_only):
        raise ValueError("--dependency-chain-only 不能与 --post-depen-gen、--resume-missing-container-divide、--api-doc-with-type-only 或 --parameter-mapping-only 同时使用")
    if args.api_doc_with_type_only and (args.post_depen_gen or args.resume_missing_container_divide or args.horizontal_test_only or args.horizontal_judgement_only):
        raise ValueError("--api-doc-with-type-only 不能与 --post-depen-gen 或 --resume-missing-container-divide 同时使用")
    if args.horizontal_test_only and (args.post_depen_gen or args.resume_missing_container_divide or args.horizontal_judgement_only):
        raise ValueError("--horizontal-test-only 不能与 --post-depen-gen 或 --resume-missing-container-divide 同时使用")
    if args.horizontal_judgement_only and (args.post_depen_gen or args.resume_missing_container_divide):
        raise ValueError("--horizontal-judgement-only 不能与 --post-depen-gen 或 --resume-missing-container-divide 同时使用")
    if args.ablation and (
        args.post_depen_gen
        or args.resume_missing_container_divide
        or args.api_doc_with_type_only
        or args.parameter_mapping_only
        or args.dependency_chain_only
        or args.horizontal_test_only
        or args.horizontal_judgement_only
    ):
        raise ValueError("--ablation 不能与 post/resume/only 模式同时使用")
    if args.parallel_projects < 1:
        raise ValueError("--parallel-projects 必须大于等于 1")
    if args.fresh and (args.post_depen_gen or args.resume_missing_container_divide or args.horizontal_test_only or args.horizontal_judgement_only):
        raise ValueError("--fresh 不能与 --post-depen-gen、--resume-missing-container-divide、--horizontal-test-only 或 --horizontal-judgement-only 同时使用")
    projects = load_projects()
    models = resolve_models(args)
    ablation_runs = resolve_ablation_runs(args)
    targets = resolve_targets(args, projects)
    overall_summary: dict[str, dict[str, list]] = {}

    for model in models:
        for ablation in ablation_runs:
            cache_root = cache_root_for_run(model, ablation)
            if not args.post_depen_gen:
                prepare_model_cache_from_template(cache_root, skip_dependency_chain=args.skip_dependency_chain)
            summary = {"success": [], "failed": [], "skipped": []}
            summary_key = model if not ablation else f"{model}/{ablation}"
            overall_summary[summary_key] = summary

            print(f"\n[model] {model}")
            if ablation:
                print(f"[ablation] {ablation}")
            print(f"[cache-root] {cache_root}")
            if args.post_depen_gen:
                print("[cache-template] post-depen-gen 模式，保留现有缓存，不重建 template")
            elif TEMPLATE_CACHE.exists():
                if args.skip_dependency_chain:
                    print(f"[cache-template] 仅同步 {TEMPLATE_CACHE} 中的 *openapi.json、openapi_formated.json、http-requests.json 和 create_request_data_packages_results.json，不清空现有缓存")
                else:
                    print(f"[cache-template] 仅同步 {TEMPLATE_CACHE} 中的 *openapi.json、openapi_formated.json 和 http-requests.json，不清空现有缓存")
            print(f"[parallel-projects] {args.parallel_projects}")

            pending_targets: list[tuple[str, str]] = []
            should_stop = False
            for target in targets:
                project_key = resolve_project_key(target, projects)
                if not project_key:
                    print(f"[skip] {target}: project.json 中未找到对应项目配置")
                    summary["skipped"].append((target, "missing-project-config"))
                    continue
                if args.fresh:
                    clear_generated_outputs(cache_root, project_key)
                    print(f"[fresh] {target}: 已清理生成产物，将重新生成")
                pending_targets.append((target, project_key))

            if should_stop:
                continue

            total_targets = len(pending_targets)
            completed_targets = 0
            print(f"[progress] 0/{total_targets} done, remaining={total_targets}")

            if args.parallel_projects == 1:
                for target, project_key in pending_targets:
                    status, name, payload = run_target_command(target, project_key, model, cache_root, args, ablation)
                    if status == "success":
                        summary["success"].append(name)
                    elif status == "skipped":
                        summary["skipped"].append((name, payload))
                    else:
                        summary["failed"].append((name, payload))

                    completed_targets += 1
                    print_progress(completed_targets, total_targets, summary)
                    if status == "failed" and not args.continue_on_error:
                        break
            else:
                with ThreadPoolExecutor(max_workers=args.parallel_projects) as executor:
                    future_to_target = {
                        executor.submit(run_target_command, target, project_key, model, cache_root, args, ablation): target
                        for target, project_key in pending_targets
                    }
                    for future in as_completed(future_to_target):
                        status, name, payload = future.result()
                        if status == "success":
                            summary["success"].append(name)
                        elif status == "skipped":
                            summary["skipped"].append((name, payload))
                        else:
                            summary["failed"].append((name, payload))
                        completed_targets += 1
                        print_progress(completed_targets, total_targets, summary)

    print("\n[summary]")
    failed_exists = False
    for model, summary in overall_summary.items():
        print(f"\n[{model}]")
        print(f"success: {len(summary['success'])}")
        for name in summary["success"]:
            print(f"  - {name}")

        print(f"failed: {len(summary['failed'])}")
        for name, code in summary["failed"]:
            failed_exists = True
            print(f"  - {name} (exit={code})")

        print(f"skipped: {len(summary['skipped'])}")
        for name, reason in summary["skipped"]:
            print(f"  - {name} ({reason})")

    return 1 if failed_exists else 0


if __name__ == "__main__":
    raise SystemExit(main())
