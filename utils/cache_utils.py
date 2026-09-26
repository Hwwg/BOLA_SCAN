import os
import re


def get_project_root(start_path: str | None = None) -> str:
    current = start_path or os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(current, ".."))


def get_cache_root(project_root: str | None = None) -> str:
    root = project_root or get_project_root()
    env_root = os.environ.get("BOLASCAN_CACHE_ROOT")
    if isinstance(env_root, str) and env_root.strip():
        return os.path.abspath(env_root.strip())
    model_name = os.environ.get("BOLASCAN_LLM_MODEL")
    if isinstance(model_name, str) and model_name.strip():
        return os.path.join(root, f"cache_{sanitize_model_name(model_name)}")
    return os.path.join(root, "cache")


def get_project_cache_dir(project_name: str, project_root: str | None = None) -> str:
    return os.path.join(get_cache_root(project_root), project_name)


def ensure_project_cache_dir(project_name: str, project_root: str | None = None) -> str:
    cache_dir = get_project_cache_dir(project_name, project_root)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def sanitize_model_name(model_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", (model_name or "").strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "unknown_model"
