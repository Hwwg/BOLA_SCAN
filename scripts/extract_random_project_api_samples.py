#!/usr/bin/env python3
import argparse
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "scripts" / "project_api_samples"
MAX_PER_PROJECT = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="随机提取 cache 下每个项目的接口样本，并按项目输出 JSON 文件"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=MAX_PER_PROJECT,
        help=f"每个项目抽取接口数量（默认: {MAX_PER_PROJECT}）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子（可选，便于复现）",
    )
    parser.add_argument(
        "--project",
        action="append",
        help="仅处理指定项目，可重复传入，或使用逗号分隔",
    )
    parser.add_argument(
        "--model",
        default="default",
        help="输出目录的模型层级名称（默认: default）",
    )
    return parser.parse_args()


def normalize_model_dir(model: str) -> str:
    value = (model or "").strip()
    if not value:
        return "default"
    return value.replace("/", "_")


def get_cache_dir(model: str) -> Path:
    model_dir = normalize_model_dir(model)
    return ROOT / f"cache_{model_dir}"


def resolve_projects(project_args: list[str] | None, cache_dir: Path) -> list[str]:
    all_projects = sorted(
        p.name for p in cache_dir.iterdir() if (p / "api_doc_with_type.json").exists()
    )
    if not project_args:
        return all_projects

    wanted: list[str] = []
    seen = set()
    for arg in project_args:
        for item in arg.split(","):
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            wanted.append(name)
    return wanted


def collect_interfaces(api_doc_data: list[dict]) -> list[dict]:
    interfaces: list[dict] = []
    for group_obj in api_doc_data:
        if not isinstance(group_obj, dict):
            continue
        for group_name, group_value in group_obj.items():
            if not isinstance(group_value, dict):
                continue
            for api_name, api_detail in group_value.items():
                if api_name == "__inherited_params__":
                    continue
                if not isinstance(api_detail, dict):
                    continue
                interfaces.append(
                    {
                        "api_name": api_name,
                        "type": api_detail.get("type"),
                    }
                )
    return interfaces


def sample_project_interfaces(
    project: str, count: int, rng: random.Random, cache_dir: Path
) -> tuple[list[dict], str | None]:
    source_file = cache_dir / project / "api_doc_with_type.json"

    if not source_file.exists():
        return [], "api_doc_with_type.json not found"

    with source_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return [], "invalid format: root is not list"

    all_interfaces = collect_interfaces(data)
    if not all_interfaces:
        return [], None

    sample_size = min(count, len(all_interfaces))
    return rng.sample(all_interfaces, sample_size), None


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        raise ValueError("--count 必须大于 0")

    rng = random.Random(args.seed)
    model_dir = normalize_model_dir(args.model)
    cache_dir = get_cache_dir(args.model)
    output_base_dir = OUTPUT_DIR / model_dir
    output_base_dir.mkdir(parents=True, exist_ok=True)

    if not cache_dir.exists():
        print(f"[warn] cache 目录不存在: {cache_dir}")
        return 0

    selected_projects = resolve_projects(args.project, cache_dir)
    if not selected_projects:
        print(f"[warn] 在 {cache_dir} 下没有找到可处理项目")
        return 0

    for project in selected_projects:
        output_file = output_base_dir / f"{project}.json"
        sampled_items, error = sample_project_interfaces(project, args.count, rng, cache_dir)
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(sampled_items, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if error is None:
            print(
                f"[ok] {project}: sampled {len(sampled_items)} -> {output_file}"
            )
        else:
            print(f"[skip] {project}: {error} -> {output_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
