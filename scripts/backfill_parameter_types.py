#!/usr/bin/env python3
"""
Backfill endpoint `type` from api_doc_with_type.json into parameters_dict_all.json
without changing any other fields.

Single-project usage:
    python scripts/backfill_parameter_types.py \
      --api-doc-with-type cache_xxx/project/api_doc_with_type.json \
      --parameters-dict cache_xxx/project/parameters_dict_all.json

Batch usage:
    python scripts/backfill_parameter_types.py \
      --cache-root cache_qwen3.6-flash-2026-04-16

Optional:
    --projects easyappointments,crapi
    --output /path/to/new_parameters_dict_all.json
    --no-backup
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只回填 parameters_dict_all.json 中的 type 字段，不改动其他数据"
    )
    parser.add_argument("--api-doc-with-type", help="api_doc_with_type.json 路径")
    parser.add_argument("--parameters-dict", help="parameters_dict_all.json 路径")
    parser.add_argument("--cache-root", help="批量模式：cache 根目录，例如 cache_qwen3.6-flash-2026-04-16")
    parser.add_argument("--projects", help="批量模式下仅处理这些项目，逗号分隔")
    parser.add_argument("--output", help="单项目模式输出路径；默认原地覆盖 parameters_dict_all.json")
    parser.add_argument("--no-backup", action="store_true", help="原地覆盖时不生成 .bak 备份")
    args = parser.parse_args()
    single_mode = bool(args.api_doc_with_type or args.parameters_dict)
    batch_mode = bool(args.cache_root)
    if single_mode and batch_mode:
        parser.error("--cache-root 不能与 --api-doc-with-type/--parameters-dict 同时使用")
    if not single_mode and not batch_mode:
        parser.error("必须提供单项目参数，或使用 --cache-root 批量模式")
    if single_mode and (not args.api_doc_with_type or not args.parameters_dict):
        parser.error("单项目模式下必须同时提供 --api-doc-with-type 和 --parameters-dict")
    if batch_mode and args.output:
        parser.error("--output 仅适用于单项目模式")
    return args


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_csv(raw: str | None) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_endpoint_key(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    method, sep, _rest = text.partition(" ")
    return bool(sep) and method in HTTP_METHODS


def build_type_map(api_doc_with_type: list[Any]) -> tuple[dict[str, str], int]:
    endpoint_type_map: dict[str, str] = {}
    total_endpoints = 0
    for group_item in api_doc_with_type:
        if not isinstance(group_item, dict):
            continue
        for _group_name, apis in group_item.items():
            if not isinstance(apis, dict):
                continue
            for endpoint, api_info in apis.items():
                if not is_endpoint_key(endpoint) or not isinstance(api_info, dict):
                    continue
                total_endpoints += 1
                api_type = api_info.get("type")
                if isinstance(api_type, str) and api_type.strip():
                    endpoint_type_map[endpoint] = api_type.strip()
    return endpoint_type_map, total_endpoints


def backfill_types(node: Any, endpoint_type_map: dict[str, str], stats: dict[str, int]) -> None:
    if isinstance(node, list):
        for item in node:
            backfill_types(item, endpoint_type_map, stats)
        return

    if not isinstance(node, dict):
        return

    for key, value in node.items():
        if is_endpoint_key(key) and isinstance(value, dict):
            api_type = endpoint_type_map.get(key)
            if api_type:
                old_type = value.get("type")
                if old_type != api_type:
                    value["type"] = api_type
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            else:
                stats["missing_type"] += 1
            backfill_types(value, endpoint_type_map, stats)
        else:
            backfill_types(value, endpoint_type_map, stats)


def backfill_one_project(
    api_doc_path: Path,
    params_path: Path,
    output_path: Path,
    no_backup: bool,
) -> dict[str, Any]:
    api_doc_with_type = read_json(api_doc_path)
    params_dict = read_json(params_path)

    endpoint_type_map, total_endpoints = build_type_map(api_doc_with_type)
    if not endpoint_type_map:
        raise RuntimeError(
            "未从 api_doc_with_type.json 中提取到任何带 type 的 endpoint："
            f" file={api_doc_path} total_endpoints={total_endpoints} typed_endpoints=0"
        )

    stats = {"updated": 0, "unchanged": 0, "missing_type": 0}
    backfill_types(params_dict, endpoint_type_map, stats)

    if output_path == params_path and not no_backup:
        backup_path = params_path.with_suffix(params_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(params_path, backup_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, params_dict)
    return {
        "updated": stats["updated"],
        "unchanged": stats["unchanged"],
        "missing_type": stats["missing_type"],
        "type_map": len(endpoint_type_map),
        "total_endpoints": total_endpoints,
        "output": str(output_path),
    }


def run_batch(cache_root: Path, projects: list[str], no_backup: bool) -> int:
    selected = set(projects) if projects else None
    summary = {"success": [], "failed": [], "skipped": []}

    for project_dir in sorted(p for p in cache_root.iterdir() if p.is_dir()):
        if selected is not None and project_dir.name not in selected:
            continue
        api_doc_path = project_dir / "api_doc_with_type.json"
        params_path = project_dir / "parameters_dict_all.json"
        if not api_doc_path.exists() or not params_path.exists():
            print(f"[skip] {project_dir.name}: 缺少 api_doc_with_type.json 或 parameters_dict_all.json")
            summary["skipped"].append(project_dir.name)
            continue
        try:
            result = backfill_one_project(api_doc_path, params_path, params_path, no_backup)
            print(
                f"[ok] {project_dir.name}: "
                f"updated={result['updated']} "
                f"unchanged={result['unchanged']} "
                f"missing_type={result['missing_type']} "
                f"type_map={result['type_map']} "
                f"total_endpoints={result['total_endpoints']}"
            )
            summary["success"].append(project_dir.name)
        except Exception as exc:
            print(f"[fail] {project_dir.name}: {exc}")
            summary["failed"].append(project_dir.name)

    print("\n[summary]")
    print(f"success={len(summary['success'])}")
    print(f"failed={len(summary['failed'])}")
    print(f"skipped={len(summary['skipped'])}")
    if summary["failed"]:
        print("failed_projects=" + ",".join(summary["failed"]))
    return 0 if not summary["failed"] else 1


def main() -> int:
    args = parse_args()
    if args.cache_root:
        return run_batch(
            Path(args.cache_root).expanduser().resolve(),
            parse_csv(args.projects),
            args.no_backup,
        )

    api_doc_path = Path(args.api_doc_with_type).expanduser().resolve()
    params_path = Path(args.parameters_dict).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else params_path
    result = backfill_one_project(api_doc_path, params_path, output_path, args.no_backup)
    if output_path == params_path and not args.no_backup:
        print(f"[backup] {params_path.with_suffix(params_path.suffix + '.bak')}")
    print(f"[api-doc-with-type] {api_doc_path}")
    print(f"[parameters-dict] {params_path}")
    print(f"[output] {output_path}")
    print(
        "[summary] "
        f"updated={result['updated']} "
        f"unchanged={result['unchanged']} "
        f"missing_type={result['missing_type']} "
        f"type_map={result['type_map']} "
        f"total_endpoints={result['total_endpoints']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
