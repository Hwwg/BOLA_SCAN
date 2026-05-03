#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from utils.cache_utils import ensure_project_cache_dir, sanitize_model_name


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_SH_PATH = os.path.join(CURRENT_DIR, "env.sh")
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)


DEFAULT_CLICK_OPTIONS = {
    "depth": 2,
    "mode": "desktop",
    "format": "json",
    "fast_mode": False,
    "parallel_pages": 3,
}

MODEL_ENDPOINT_ADAPTERS = {
    # Force OpenAI-compatible gateway and chat.completions endpoint.
    "claude-haiku-4-5-20251001": {
        "provider": "openai",
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gemini-2.5-flash-preview-09-2025": {
        "provider": "openai",
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gpt-5-mini": {
        "provider": "openai",
        "base_url": "https://aigc.x-see.cn/v1",
        "endpoint_mode": "chat",
        "api_key_env": "OPENAI_API_KEY",
    },
    # Force DeepSeek official endpoint and chat.completions mode.
    "deepseek-chat": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "endpoint_mode": "deepseek_chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen3.6-plus": {
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
    "qwen3.6-flash": {
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
    "qwen3.5-plus-2026-02-15": {
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "responses",
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_s": "180",
        "max_workers": "1",
        "max_retries": "2",
    },
}


def normalize_model_name(model_name: str | None) -> str:
    return (model_name or "").strip().lower()


def get_model_adapter(model_name: str | None) -> dict[str, str] | None:
    model_key = normalize_model_name(model_name)
    adapter = MODEL_ENDPOINT_ADAPTERS.get(model_key)
    if adapter:
        return adapter
    if model_key.startswith("qwen") or model_key.startswith("qw"):
        return {
            "provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "endpoint_mode": "responses",
            "api_key_env": "DASHSCOPE_API_KEY",
            "timeout_s": "180",
            "max_workers": "1",
            "max_retries": "2",
        }
    return None


def load_env_sh(env_path: str = ENV_SH_PATH, target_env: Dict[str, str] | None = None) -> Dict[str, str]:
    env = target_env if target_env is not None else os.environ
    if not os.path.exists(env_path):
        return env

    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                env[key] = value
    except Exception as exc:
        print(f"[warn] 加载 env.sh 失败: {exc}", file=sys.stderr)
    return env


def resolve_llm_provider(model_name: str | None) -> str:
    adapter = get_model_adapter(model_name)
    if adapter:
        return adapter["provider"]
    model = normalize_model_name(model_name)
    if "deepseek" in model:
        return "deepseek"
    if model.startswith("qwen") or model.startswith("qw"):
        return "qwen"
    if model.startswith("gpt-") or "openai" in model:
        return "openai"
    return "generic"


def get_login_url(project: Dict[str, Any]) -> str:
    login_url = project.get("login_url")
    if isinstance(login_url, str) and login_url.strip():
        return login_url.strip()

    url = project.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()

    raise ValueError("项目配置缺少 url/login_url，无法确定点击采集入口")


def get_bola_url(project: Dict[str, Any]) -> str:
    url = project.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    raise ValueError("项目配置缺少 url，无法执行 BOLA 阶段")


def apply_llm_env(project: Dict[str, Any], env: Dict[str, str] | None = None) -> Dict[str, str]:
    target_env = env if env is not None else os.environ
    load_env_sh(ENV_SH_PATH, target_env)
    llm_config = project.get("llm_config") or project.get("llm") or {}

    model = llm_config.get("model") or project.get("model")
    provider = resolve_llm_provider(model)
    adapter = get_model_adapter(model)

    api_key = None
    base_url = None
    if provider == "deepseek":
        api_key = target_env.get("DEEPSEEK_API_KEY")
        base_url = target_env.get("DEEPSEEK_BASE_URL")
    elif provider == "openai":
        api_key = target_env.get("OPENAI_API_KEY")
        base_url = target_env.get("OPENAI_BASE_URL")

    if not api_key:
        api_key = (
            target_env.get("BOLASCAN_LLM_API_KEY")
            or llm_config.get("api_key")
            or llm_config.get("apiKey")
        )
    if not base_url:
        base_url = (
            target_env.get("BOLASCAN_LLM_BASE_URL")
            or llm_config.get("base_url")
            or llm_config.get("baseURL")
        )

    if adapter:
        forced_key = target_env.get(adapter["api_key_env"])
        if isinstance(forced_key, str) and forced_key.strip():
            api_key = forced_key.strip()
        base_url = adapter["base_url"]
        target_env["BOLASCAN_LLM_ENDPOINT_MODE"] = adapter["endpoint_mode"]
    else:
        target_env.pop("BOLASCAN_LLM_ENDPOINT_MODE", None)

    # 环境变量优先：如果外部已显式设置，则不被 project.json 覆盖
    if isinstance(api_key, str) and api_key.strip():
        target_env["BOLASCAN_LLM_API_KEY"] = api_key.strip()
    if isinstance(base_url, str) and base_url.strip():
        target_env["BOLASCAN_LLM_BASE_URL"] = base_url.strip()
    timeout_s = adapter.get("timeout_s") if adapter else None
    if isinstance(timeout_s, str) and timeout_s.strip() and not target_env.get("BOLASCAN_LLM_TIMEOUT"):
        target_env["BOLASCAN_LLM_TIMEOUT"] = timeout_s.strip()
    max_workers = adapter.get("max_workers") if adapter else None
    if isinstance(max_workers, str) and max_workers.strip() and not target_env.get("BOLASCAN_LLM_MAX_WORKERS"):
        target_env["BOLASCAN_LLM_MAX_WORKERS"] = max_workers.strip()
    max_retries = adapter.get("max_retries") if adapter else None
    if isinstance(max_retries, str) and max_retries.strip() and not target_env.get("BOLASCAN_LLM_MAX_RETRIES"):
        target_env["BOLASCAN_LLM_MAX_RETRIES"] = max_retries.strip()
    if isinstance(model, str) and model.strip():
        target_env["BOLASCAN_LLM_MODEL"] = model.strip()

    if not target_env.get("BOLASCAN_LLM_TEMPERATURE"):
        temperature = llm_config.get("temperature")
        if temperature is None:
            temperature = 0
        try:
            target_env["BOLASCAN_LLM_TEMPERATURE"] = str(float(temperature))
        except (TypeError, ValueError):
            target_env["BOLASCAN_LLM_TEMPERATURE"] = "0"

    return target_env


def has_usable_fallback_auth(fallback_auth: Dict[str, Any] | None) -> bool:
    if not isinstance(fallback_auth, dict):
        return False

    candidate_values = [
        fallback_auth.get("token"),
        fallback_auth.get("authorization"),
        fallback_auth.get("Authorization"),
        fallback_auth.get("cookie"),
        fallback_auth.get("Cookie"),
    ]
    for value in candidate_values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        if "replace-with-" in stripped.lower():
            continue
        if stripped.lower() in {"bearer xxx", "xxx"}:
            continue
        return True
    return False


def resolve_fallback_header(fallback_auth: Dict[str, Any]) -> tuple[str, str, str]:
    token = fallback_auth.get("token")
    if isinstance(token, str) and token.strip():
        return (
            token.strip(),
            fallback_auth.get("token_header", "Authorization"),
            fallback_auth.get("token_prefix", "Bearer "),
        )

    for key in ("authorization", "Authorization"):
        value = fallback_auth.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("bearer "):
            return (stripped[7:], "Authorization", "Bearer ")
        return (stripped, "Authorization", "")

    for key in ("cookie", "Cookie"):
        value = fallback_auth.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return (stripped, "Cookie", "")

    ignored_keys = {
        "token",
        "token_header",
        "token_prefix",
        "token_storage_key",
        "token_storage_target",
        "token_cookie_name",
    }
    for key, value in fallback_auth.items():
        if key in ignored_keys or not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return (
                stripped,
                key,
                "" if key.lower() == "cookie" else fallback_auth.get("token_prefix", ""),
            )

    raise ValueError("fallback_auth 缺少可识别的 header/token 配置")


def build_cache_fallback_project(project_name: str) -> Dict[str, Any]:
    cache_dir = ensure_cache_dir(project_name)
    return {
        "project_name": project_name,
        "model": os.environ.get("BOLASCAN_LLM_MODEL") or "gpt-4o-mini",
        "openapi_doc": os.path.join(cache_dir, f"{project_name}_openapi.json"),
        "url": "",
        "click_account": {},
        "test_account": {},
        "data_account": {},
    }


def load_project_config(config_path: str, project_name: str, allow_cache_fallback: bool = False) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    projects = raw.get("projects", {})
    if project_name in projects:
        project = projects[project_name]
        if not isinstance(project, dict):
            raise ValueError(f"项目配置格式错误: {project_name}")
        return project

    if allow_cache_fallback:
        return build_cache_fallback_project(project_name)

    raise ValueError(f"project.json 中未找到项目: {project_name}")


def ensure_cache_dir(project_name: str) -> str:
    return ensure_project_cache_dir(project_name, CURRENT_DIR)


def read_requests_count(requests_path: str) -> int:
    if not os.path.exists(requests_path):
        return 0
    try:
        with open(requests_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        all_requests = payload.get("allRequests", [])
        return len(all_requests) if isinstance(all_requests, list) else 0
    except Exception:
        return 0


def build_click_command(
    url: str,
    output_dir: str,
    click_config: Dict[str, Any],
    use_password: bool,
    auth_fallback: Dict[str, Any] | None,
) -> list[str]:
    merged = dict(DEFAULT_CLICK_OPTIONS)
    merged.update(click_config.get("options", {}))

    command = [
        "node",
        os.path.join(CURRENT_DIR, "automated_click", "index.js"),
        "--url",
        url,
        "--output",
        output_dir,
        "--depth",
        str(merged["depth"]),
        "--format",
        merged["format"],
        "--mode",
        merged["mode"],
        "--parallel-pages",
        str(merged["parallel_pages"]),
    ]

    if merged.get("fast_mode"):
        command.append("--fast-mode")

    if use_password:
        username = click_config.get("username")
        password = click_config.get("password")
        if not username or not password:
            raise ValueError("click_account 缺少 username/password，无法执行密码登录采集")
        command.extend(["--username", username, "--password", password])
        return command

    fallback_auth = auth_fallback or {}
    token, token_header, token_prefix = resolve_fallback_header(fallback_auth)
    command.extend(["--token", token])
    command.extend(["--token-header", token_header])
    command.extend(["--token-prefix", token_prefix])

    token_storage_key = fallback_auth.get("token_storage_key")
    token_storage_target = fallback_auth.get("token_storage_target")
    token_cookie_name = fallback_auth.get("token_cookie_name")
    if token_storage_key:
        command.extend(["--token-storage-key", token_storage_key])
    if token_storage_target:
        command.extend(["--token-storage-target", token_storage_target])
    if token_cookie_name:
        command.extend(["--token-cookie-name", token_cookie_name])

    return command


def run_click_collection(project: Dict[str, Any], cache_dir: str) -> str:
    login_url = get_login_url(project)

    click_config = project.get("click_account", {})
    requests_path = os.path.join(cache_dir, "http-requests.json")
    command_env = apply_llm_env(project, os.environ.copy())

    print(f"[collect] 开始密码登录点击采集: {project.get('project_name')}")
    password_cmd = build_click_command(
        url=login_url,
        output_dir=cache_dir,
        click_config=click_config,
        use_password=True,
        auth_fallback=None,
    )
    first_run = subprocess.run(password_cmd, cwd=CURRENT_DIR, check=False, env=command_env)
    request_count = read_requests_count(requests_path)
    if first_run.returncode == 0 and request_count > 0:
        print(f"[collect] 密码登录采集完成，捕获请求数: {request_count}")
        return requests_path

    fallback_auth = click_config.get("fallback_auth")
    if not has_usable_fallback_auth(fallback_auth):
        raise RuntimeError(
            "密码登录点击采集失败，且未配置可用的 click_account.fallback_auth，无法继续回退采集"
        )

    print("[collect] 密码登录采集失败，开始使用 fallback_auth 回退采集")
    fallback_cmd = build_click_command(
        url=login_url,
        output_dir=cache_dir,
        click_config=click_config,
        use_password=False,
        auth_fallback=fallback_auth,
    )
    second_run = subprocess.run(fallback_cmd, cwd=CURRENT_DIR, check=False, env=command_env)
    request_count = read_requests_count(requests_path)
    if second_run.returncode == 0 and request_count > 0:
        print(f"[collect] fallback_auth 采集完成，捕获请求数: {request_count}")
        return requests_path

    raise RuntimeError("点击采集失败：密码登录和 fallback_auth 两条路径都未生成 http-requests.json")


def prepare_openapi(project: Dict[str, Any], cache_dir: str) -> str:
    openapi_doc = project.get("openapi_doc")
    candidate_paths = []
    candidate_paths.extend(
        [
            os.path.join(cache_dir, "openapi_formated.json"),
            os.path.join(cache_dir, f"{project.get('project_name')}_openapi.json"),
        ]
    )
    candidate_paths.extend(
        str(path)
        for path in sorted(Path(cache_dir).glob("*openapi.json"))
        if path.is_file()
    )
    if isinstance(openapi_doc, str) and openapi_doc.strip():
        candidate_paths.append(openapi_doc.strip())

    resolved_openapi = next((path for path in candidate_paths if path and os.path.exists(path)), None)
    if not resolved_openapi:
        raise FileNotFoundError(
            "未找到 OpenAPI 文档。请配置 project.openapi_doc，或将文档放到 "
            f"{cache_dir} 下的 *openapi.json"
        )

    destination = os.path.join(cache_dir, "openapi_formated.json")
    if os.path.abspath(resolved_openapi) == os.path.abspath(destination):
        return destination

    if os.path.basename(resolved_openapi) == "openapi_formated.json":
        if os.path.abspath(resolved_openapi) != os.path.abspath(destination):
            shutil.copyfile(resolved_openapi, destination)
        return destination

    convert_cmd = [
        "npx",
        "openapi2postmanv2",
        "-s",
        resolved_openapi,
        "-o",
        destination,
        "-p",
        "-O",
        "folderStrategy=Paths",
    ]
    result = subprocess.run(convert_cmd, cwd=CURRENT_DIR, check=False)
    if result.returncode != 0 or not os.path.exists(destination):
        raise RuntimeError("OpenAPI 转换失败，请检查 openapi_doc 是否有效")
    return destination


def build_auth_type(project: Dict[str, Any]) -> Dict[str, Any]:
    test_auth = project.get("test_account", {}).get("auth")
    data_auth = project.get("data_account", {}).get("auth")
    if not test_auth or not data_auth:
        raise ValueError("BOLA 阶段需要同时配置 test_account.auth 和 data_account.auth")
    return {
        "test_account": {"auth": test_auth},
        "data_account": {"auth": data_auth},
    }


def normalize_api_path_blacklist(project: Dict[str, Any]) -> list[str]:
    candidates = (
        project.get("api_path_blacklist")
        or project.get("api_blacklist")
        or project.get("path_blacklist")
        or []
    )
    if not isinstance(candidates, list):
        return []

    normalized: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        prefix = item.strip()
        if not prefix:
            continue
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        normalized.append(prefix)
    return normalized


def run_bola(project: Dict[str, Any], cache_dir: str, mode: str = "full") -> None:
    apply_llm_env(project)
    from utils.dependency_cc.main import run_dependency_generation

    requests_path = os.path.join(cache_dir, "http-requests.json")
    if mode not in {"api-doc-with-type-only", "parameter-mapping-only", "dependency-chain-only"} and read_requests_count(requests_path) == 0:
        raise RuntimeError("缺少有效的 http-requests.json，无法执行 BOLA 阶段")

    api_doc_path = prepare_openapi(project, cache_dir)
    config = {
        "api_doc_path": api_doc_path,
        "model": project.get("model"),
        "case_file_path": requests_path,
        "url": get_bola_url(project) if mode == "full" else project.get("url", ""),
        "auth_type": build_auth_type(project) if mode == "full" else {},
        "project_name": project.get("project_name"),
        "api_path_blacklist": normalize_api_path_blacklist(project),
        "mode": mode,
    }
    if not config["model"]:
        raise ValueError("项目配置缺少 model，无法执行 BOLA 阶段")

    print(f"[bola] 开始执行 dependency_cc 流程: {config['project_name']} (mode={mode})")
    run_dependency_generation(config)


def run_container_divide_only(project: Dict[str, Any], cache_dir: str, force: bool = False) -> str:
    apply_llm_env(project)
    from scripts.jsontools import JsonTools
    from utils.bola_vulner.horizontal.horizontal_vuln import HorizontalVuln

    horizontal_results_dir = os.path.join(cache_dir, "horizontal_results")
    os.makedirs(horizontal_results_dir, exist_ok=True)
    target_path = os.path.join(horizontal_results_dir, "container_resource_divide_results.json")
    companion_paths = [
        os.path.join(horizontal_results_dir, "data_resource_id_result.json"),
        os.path.join(horizontal_results_dir, "container_reoust_id_result.json"),
        target_path,
    ]

    if not force and all(os.path.exists(path) for path in companion_paths):
        print(f"[container-divide] 三个结果文件都已存在，跳过: {target_path}")
        return target_path
    if force:
        print(f"[container-divide] force 模式开启，忽略现有结果并重新生成: {target_path}")

    params_dict_path = os.path.join(cache_dir, "parameters_dict_all.json")
    case_packages_path = os.path.join(cache_dir, "create_request_data_packages_results.json")
    api_doc_with_type_path = os.path.join(cache_dir, "api_doc_with_type.json")
    required_files = [
        params_dict_path,
        case_packages_path,
        api_doc_with_type_path,
    ]
    missing_files = [path for path in required_files if not os.path.exists(path)]
    if missing_files:
        raise FileNotFoundError(
            "container-divide-only 缺少前置文件: " + ", ".join(missing_files)
        )

    jsontool = JsonTools()
    horiontest = HorizontalVuln(
        project.get("model"),
        jsontool.read_json(params_dict_path),
        jsontool.read_json(case_packages_path),
        project.get("project_name"),
        jsontool.read_json(api_doc_with_type_path),
    )
    horiontest.generate_container_resource_divide_results()
    if not os.path.exists(target_path):
        raise RuntimeError("container_resource_divide_results.json 生成失败")
    print(f"[container-divide] 生成完成: {target_path}")
    return target_path


def run_horizontal_test_only(project: Dict[str, Any], cache_dir: str) -> str:
    """复用已识别的 identifier/container divide 结果，只执行水平 BOLA 测试阶段。"""
    apply_llm_env(project)
    from scripts.jsontools import JsonTools
    from utils.bola_vulner.horizontal.horizontal_vuln import HorizontalVuln
    from utils.bola_vulner.horizontal.utils_helpers import make_json_serializable

    horizontal_results_dir = os.path.join(cache_dir, "horizontal_results")
    os.makedirs(horizontal_results_dir, exist_ok=True)

    params_dict_path = os.path.join(cache_dir, "parameters_dict_all.json")
    case_packages_path = os.path.join(cache_dir, "create_request_data_packages_results.json")
    api_doc_with_type_path = os.path.join(cache_dir, "api_doc_with_type.json")
    container_divide_path = os.path.join(horizontal_results_dir, "container_resource_divide_results.json")
    required_files = [
        params_dict_path,
        case_packages_path,
        api_doc_with_type_path,
        container_divide_path,
    ]
    missing_files = [path for path in required_files if not os.path.exists(path)]
    if missing_files:
        raise FileNotFoundError(
            "horizontal-test-only 缺少前置文件: " + ", ".join(missing_files)
        )

    jsontool = JsonTools()
    horiontest = HorizontalVuln(
        project.get("model"),
        jsontool.read_json(params_dict_path),
        jsontool.read_json(case_packages_path),
        project.get("project_name"),
        jsontool.read_json(api_doc_with_type_path),
    )

    container_resource_divide_results = jsontool.read_json(container_divide_path)
    dependency_execution_routes_packages = horiontest.dependency_chain_package_generation(
        container_resource_divide_results
    )
    jsontool.write_json(
        os.path.join(horizontal_results_dir, "dependency_execution_reoutes_packages.json"),
        make_json_serializable(dependency_execution_routes_packages),
    )
    all_account_execution_results = horiontest.execution_packages(
        get_bola_url(project),
        build_auth_type(project),
        dependency_execution_routes_packages,
        container_resource_divide_results,
    )
    serializable_execution_results = make_json_serializable(all_account_execution_results)
    jsontool.write_json(
        os.path.join(horizontal_results_dir, "all_acount_execution_results.json"),
        serializable_execution_results,
    )
    vulnerability_results = horiontest.bola_vul_judgement(serializable_execution_results)
    target_path = os.path.join(cache_dir, "bola_horizontal_results.json")
    jsontool.write_json(target_path, make_json_serializable(vulnerability_results))
    print(f"[horizontal-test] 生成完成: {target_path}")
    return target_path


def run_horizontal_judgement_only(project: Dict[str, Any], cache_dir: str) -> str:
    """复用水平测试执行证据，只重新执行最终 BOLA 语义校验/判定阶段。"""
    apply_llm_env(project)
    from scripts.jsontools import JsonTools
    from utils.bola_vulner.horizontal.horizontal_vuln import HorizontalVuln
    from utils.bola_vulner.horizontal.utils_helpers import make_json_serializable

    horizontal_results_dir = os.path.join(cache_dir, "horizontal_results")
    params_dict_path = os.path.join(cache_dir, "parameters_dict_all.json")
    case_packages_path = os.path.join(cache_dir, "create_request_data_packages_results.json")
    api_doc_with_type_path = os.path.join(cache_dir, "api_doc_with_type.json")
    execution_results_path = os.path.join(horizontal_results_dir, "all_acount_execution_results.json")
    container_divide_path = os.path.join(horizontal_results_dir, "container_resource_divide_results.json")
    required_files = [
        params_dict_path,
        case_packages_path,
        api_doc_with_type_path,
        execution_results_path,
        container_divide_path,
    ]
    missing_files = [path for path in required_files if not os.path.exists(path)]
    if missing_files:
        raise FileNotFoundError(
            "horizontal-judgement-only 缺少前置文件: " + ", ".join(missing_files)
        )

    jsontool = JsonTools()
    horiontest = HorizontalVuln(
        project.get("model"),
        jsontool.read_json(params_dict_path),
        jsontool.read_json(case_packages_path),
        project.get("project_name"),
        jsontool.read_json(api_doc_with_type_path),
    )
    container_resource_divide_results = jsontool.read_json(container_divide_path)
    try:
        horiontest.container_params_by_group = horiontest.build_container_params_by_group(
            container_resource_divide_results
        )
    except Exception:
        horiontest.container_params_by_group = {"ou_id": {}, "resource_id": {}}

    execution_results = jsontool.read_json(execution_results_path)
    vulnerability_results = horiontest.bola_vul_judgement(execution_results)
    target_path = os.path.join(cache_dir, "bola_horizontal_results.json")
    jsontool.write_json(target_path, make_json_serializable(vulnerability_results))
    print(f"[horizontal-judgement] 生成完成: {target_path}")
    return target_path


def run_static_identifier_only(project: Dict[str, Any], cache_dir: str) -> str:
    apply_llm_env(project)
    from gptreply.gpt_con import GPTReply
    from prompt.synthesis_prompt import SyntheticPrompt
    from scripts.jsontools import JsonTools
    from utils.bola_vulner.horizontal.resource_identifier import ResourceIdentifier
    from utils.dependency_cc.src.api_data_tag import ApiDataTagging

    horizontal_results_dir = os.path.join(cache_dir, "horizontal_results")
    os.makedirs(horizontal_results_dir, exist_ok=True)

    api_doc_path = prepare_openapi(project, cache_dir)
    api_tagger = ApiDataTagging(
        api_doc_path,
        project.get("model"),
        grouping_strategy="none",
        excludes=normalize_api_path_blacklist(project),
    )
    api_doc_with_types = api_tagger.complete_api_tagging_by_static_rules()

    jsontool = JsonTools()
    jsontool.write_json(os.path.join(cache_dir, "api_doc_with_type.json"), api_doc_with_types)

    identifier = ResourceIdentifier(
        true_params=api_doc_with_types,
        normalized_params={},
        case_generation_results_packages={},
        gpt_reply=GPTReply(project.get("model")),
        syn_prompt=SyntheticPrompt(),
        jsontool=jsontool,
        llm_dict={},
    )
    oip_result = identifier.build_oip_candidates(include_llm=False)
    data_resource_id_result = oip_result.get("by_group", {})
    oip_set = set(oip_result.get("all", []))
    hierarchy_report = identifier.build_identifier_hierarchy_report(oip_set=oip_set)
    container_params = sorted(identifier._coip_candidates_by_hierarchy(oip_set=oip_set))
    regular_by_group = {}
    container_by_group = {}
    for group_name, params in data_resource_id_result.items():
        params_set = set(params if isinstance(params, list) else [])
        regular = sorted(params_set - set(container_params))
        container = sorted(params_set & set(container_params))
        if regular:
            regular_by_group[group_name] = regular
        if container:
            container_by_group[group_name] = container

    divide_results = {
        "ou_id": [{group_name: params} for group_name, params in container_by_group.items()],
        "resource_id": [{group_name: params} for group_name, params in regular_by_group.items()],
    }
    jsontool.write_json(os.path.join(horizontal_results_dir, "oip_candidates_result.json"), oip_result)
    jsontool.write_json(os.path.join(horizontal_results_dir, "identifier_hierarchy_result.json"), hierarchy_report)
    jsontool.write_json(os.path.join(horizontal_results_dir, "data_resource_id_result.json"), data_resource_id_result)
    jsontool.write_json(os.path.join(horizontal_results_dir, "container_reoust_id_result.json"), container_params)
    jsontool.write_json(os.path.join(horizontal_results_dir, "container_resource_divide_results.json"), divide_results)
    print(f"[static-identifier] 生成完成: {horizontal_results_dir}")
    return horizontal_results_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一执行点击采集与 BOLA 测试流程")
    parser.add_argument("--project", required=True, help="项目名；在 --depen-gen 模式下可直接对应 cache/<project>")
    parser.add_argument(
        "--config",
        default=os.path.join(CURRENT_DIR, "project.json"),
        help="项目配置文件路径，默认使用仓库根目录 project.json",
    )
    parser.add_argument("--collect-only", action="store_true", help="只执行点击采集（默认行为）")
    parser.add_argument(
        "--with-bola",
        action="store_true",
        help="已禁用：不再支持同一次命令先点击采集再继续执行 BOLA，请改为先 --collect-only 再 --bola-only",
    )
    parser.add_argument("--bola-only", action="store_true", help="只执行 BOLA 阶段")
    parser.add_argument("--depen-gen", action="store_true", help="只执行依赖生成模式，停在参数填充依赖序列，不进入 BOLA 判定")
    parser.add_argument("--api-doc-with-type-only", action="store_true", help="只重新生成 api_doc_with_type.json，生成后立即停止")
    parser.add_argument("--parameter-mapping-only", action="store_true", help="只重新生成 parameters_dict_all.json，生成后立即停止")
    parser.add_argument("--dependency-chain-only", action="store_true", help="只重新生成 dependency_chains_results.json，要求前置的 api_doc_with_type.json 和 parameters_dict_all.json 已存在")
    parser.add_argument("--ablation-no-group", action="store_true", help="消融：不做功能组分组，全部 API 作为同一组继续执行到 CADS")
    parser.add_argument("--ablation-no-param-mapping", action="store_true", help="消融：保留功能组分组，但关闭 LLM 参数映射，直接使用原始参数")
    parser.add_argument("--ablation-static-api-type", action="store_true", help="消融：API 类型只用静态规则打标，不调用 LLM 判定类型")
    parser.add_argument("--static-identifier-only", action="store_true", help="消融：只用静态规则识别 identifier parameters，并写入 horizontal_results")
    parser.add_argument("--with-container-divide", action="store_true", help="在依赖生成实验中额外生成 container_resource_divide_results.json")
    parser.add_argument("--container-divide-only", action="store_true", help="只补生成 container_resource_divide_results.json，要求前置产物已存在")
    parser.add_argument("--horizontal-test-only", action="store_true", help="复用已识别的 identifier/container divide 结果，只执行水平 BOLA 测试阶段")
    parser.add_argument("--horizontal-judgement-only", action="store_true", help="复用水平测试执行证据，只重新执行最终 BOLA 语义校验/判定阶段")
    parser.add_argument("--force-container-divide", action="store_true", help="强制重新生成 container divide 结果，即使目标文件已存在也不跳过")
    parser.add_argument("--skip-dependency-chain", action="store_true", help="跳过依赖序列生成（Step 3），优先复用已有 dependency_chains_results.json")
    parser.add_argument("--refine-max-depth", type=int, help="递归细分功能组的最大深度；默认不指定/不限制，传正整数表示限制深度")
    parser.add_argument("--model", help="覆盖项目配置中的模型")
    parser.add_argument("--cache-root", help="覆盖默认 cache 根目录，例如 /path/to/cache_gpt_4o_mini")
    return parser.parse_args()


def main() -> int:
    load_env_sh()
    args = parse_args()
    if args.collect_only and args.bola_only:
        raise ValueError("--collect-only 和 --bola-only 不能同时使用")
    if args.with_bola:
        raise ValueError(
            "出于登录态隔离考虑，run_scan.py 不再支持“点击采集 + BOLA”同次串行执行。"
            "请改为分两步运行：1) --collect-only 先采集；2) --bola-only 再做 BOLA 探测。"
        )
    if args.with_bola and args.bola_only:
        raise ValueError("--with-bola 和 --bola-only 不能同时使用")
    if args.depen_gen and args.with_bola:
        raise ValueError("--depen-gen 和 --with-bola 不能同时使用")
    if args.depen_gen and args.collect_only:
        raise ValueError("--depen-gen 和 --collect-only 不能同时使用")
    ablation_flags = [
        args.ablation_no_group,
        args.ablation_no_param_mapping,
        args.ablation_static_api_type,
        args.static_identifier_only,
    ]
    if sum(1 for flag in ablation_flags if flag) > 1:
        raise ValueError("一次只能选择一个 ablation 模式")
    if args.api_doc_with_type_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or args.container_divide_only or args.horizontal_judgement_only or args.dependency_chain_only or any(ablation_flags)):
        raise ValueError("--api-doc-with-type-only 不能与 collect/bola/depen-gen/container-divide-only 模式混用")
    if args.parameter_mapping_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or args.container_divide_only or args.horizontal_judgement_only or args.api_doc_with_type_only or args.dependency_chain_only or any(ablation_flags)):
        raise ValueError("--parameter-mapping-only 不能与 collect/bola/depen-gen/container-divide-only/api-doc-with-type-only 模式混用")
    if args.dependency_chain_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or args.container_divide_only or args.horizontal_judgement_only or args.api_doc_with_type_only or args.parameter_mapping_only or any(ablation_flags)):
        raise ValueError("--dependency-chain-only 不能与 collect/bola/depen-gen/container-divide-only/api-doc-with-type-only/parameter-mapping-only 模式混用")
    if args.with_container_divide and not args.depen_gen:
        raise ValueError("--with-container-divide 当前仅支持与 --depen-gen 搭配使用")
    if args.container_divide_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or args.horizontal_test_only or args.horizontal_judgement_only or any(ablation_flags)):
        raise ValueError("--container-divide-only 不能与 collect/bola/depen-gen 模式混用")
    if args.horizontal_test_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or args.horizontal_judgement_only or any(ablation_flags)):
        raise ValueError("--horizontal-test-only 不能与 collect/bola/depen-gen 模式混用")
    if args.horizontal_judgement_only and (args.collect_only or args.with_bola or args.bola_only or args.depen_gen or any(ablation_flags)):
        raise ValueError("--horizontal-judgement-only 不能与 collect/bola/depen-gen 模式混用")
    if args.skip_dependency_chain:
        os.environ["BOLASCAN_SKIP_DEPENDENCY_CHAIN"] = "1"
    if args.refine_max_depth is not None:
        os.environ["BOLASCAN_REFINE_MAX_DEPTH"] = str(args.refine_max_depth)

    project = load_project_config(
        args.config,
        args.project,
        allow_cache_fallback=args.depen_gen or args.api_doc_with_type_only or args.parameter_mapping_only or args.dependency_chain_only or args.horizontal_test_only or args.horizontal_judgement_only,
    )
    if isinstance(args.model, str) and args.model.strip():
        project["model"] = args.model.strip()
    project_name = project.get("project_name") or args.project
    project["project_name"] = project_name
    model_name = project.get("model") or os.environ.get("BOLASCAN_LLM_MODEL") or ""
    if isinstance(model_name, str) and model_name.strip():
        os.environ["BOLASCAN_LLM_MODEL"] = model_name.strip()

    if isinstance(args.cache_root, str) and args.cache_root.strip():
        os.environ["BOLASCAN_CACHE_ROOT"] = os.path.abspath(args.cache_root.strip())
    elif not os.environ.get("BOLASCAN_CACHE_ROOT") and isinstance(model_name, str) and model_name.strip():
        os.environ["BOLASCAN_CACHE_ROOT"] = os.path.join(
            CURRENT_DIR,
            f"cache_{sanitize_model_name(model_name)}",
        )

    cache_dir = ensure_cache_dir(project_name)
    horizontal_results_dir = os.path.join(cache_dir, "horizontal_results")
    os.makedirs(horizontal_results_dir, exist_ok=True)
    os.environ.setdefault("BOLASCAN_LLM_AUDIT", "1")
    os.environ["BOLASCAN_PROJECT_NAME"] = str(project_name)
    os.environ["BOLASCAN_RUN_MODE"] = (
        "ablation" if any(ablation_flags) else
        "static-identifier-only" if args.static_identifier_only else
        "container-divide-only" if args.container_divide_only else
        "horizontal-test-only" if args.horizontal_test_only else
        "horizontal-judgement-only" if args.horizontal_judgement_only else
        "depen-gen" if args.depen_gen else
        "api-doc-with-type-only" if args.api_doc_with_type_only else
        "parameter-mapping-only" if args.parameter_mapping_only else
        "dependency-chain-only" if args.dependency_chain_only else
        "bola-only" if args.bola_only else
        "full"
    )
    if args.ablation_no_group:
        os.environ["BOLASCAN_ABLATION"] = "no-group"
    elif args.ablation_no_param_mapping:
        os.environ["BOLASCAN_ABLATION"] = "no-param-mapping"
    elif args.ablation_static_api_type:
        os.environ["BOLASCAN_ABLATION"] = "static-api-type"
    elif args.static_identifier_only:
        os.environ["BOLASCAN_ABLATION"] = "static-identifier"
    else:
        os.environ.pop("BOLASCAN_ABLATION", None)
    os.environ["BOLASCAN_LLM_AUDIT_PATH"] = os.path.join(
        horizontal_results_dir,
        "llm_call_audit.jsonl",
    )

    if args.static_identifier_only:
        run_static_identifier_only(project, cache_dir)
        print(f"[done] 流程执行完成: {project_name}")
        return 0

    if args.container_divide_only:
        run_container_divide_only(project, cache_dir, force=args.force_container_divide)
        print(f"[done] 流程执行完成: {project_name}")
        return 0
    if args.horizontal_test_only:
        run_horizontal_test_only(project, cache_dir)
        print(f"[done] 流程执行完成: {project_name}")
        return 0
    if args.horizontal_judgement_only:
        run_horizontal_judgement_only(project, cache_dir)
        print(f"[done] 流程执行完成: {project_name}")
        return 0

    should_run_collect = not args.bola_only and not args.depen_gen and not args.api_doc_with_type_only and not args.parameter_mapping_only and not args.dependency_chain_only and not args.horizontal_judgement_only and not any(ablation_flags)
    should_run_bola = args.bola_only or args.with_bola or args.depen_gen or args.api_doc_with_type_only or args.parameter_mapping_only or args.dependency_chain_only or any(ablation_flags)

    if should_run_collect:
        run_click_collection(project, cache_dir)

    if should_run_bola:
        if args.api_doc_with_type_only:
            mode = "api-doc-with-type-only"
        elif args.parameter_mapping_only:
            mode = "parameter-mapping-only"
        elif args.dependency_chain_only:
            mode = "dependency-chain-only"
        elif args.depen_gen and args.with_container_divide:
            mode = "depen-gen-with-container-divide"
        elif args.ablation_no_group:
            mode = "ablation-no-group"
        elif args.ablation_no_param_mapping:
            mode = "ablation-no-param-mapping"
        elif args.ablation_static_api_type:
            mode = "ablation-static-api-type"
        else:
            mode = "depen-gen" if args.depen_gen else "full"
        run_bola(project, cache_dir, mode=mode)

    print(f"[done] 流程执行完成: {project_name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
