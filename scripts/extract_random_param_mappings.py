#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT / "scripts" / "test_sets_mapping_parameters"

# 来源: batch_depen_gen.py#L12-27
TARGET_PROJECTS = [
    "gin-vue-blog",
        "mall",
    "JeecgBoot",
    "youlai-mall",
    "newbee-mall-plus",
    "mall-swarm",
    "newbee_mall",
    "pybbs",
    "TIME-SEA-chatgpt",
    "ctfd",
    "gin-vue-admin",
    "openemr",
    "crapi",
    "easyappointments",
]

# TARGET_PROJECTS = [
#     "openemr"
# ]

GENERIC_PARAM_NAMES = {"id", "ids", "Id"}
MAX_PER_PROJECT = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="提取各项目功能组内通用ID参数，并写入 test_sets_mapping_parameters"
    )
    parser.add_argument(
        "--project",
        action="append",
        help=(
            "只处理指定项目。可重复传参，或使用逗号分隔，"
            "例如: --project gin-vue-admin --project ctfd 或 --project gin-vue-admin,ctfd"
        ),
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


def resolve_projects(project_args: list[str] | None) -> list[str]:
    if not project_args:
        return TARGET_PROJECTS
    projects = []
    seen = set()
    for arg in project_args:
        for item in arg.split(","):
            name = item.strip()
            if not name:
                continue
            if name not in seen:
                seen.add(name)
                projects.append(name)
    return projects


def normalize_field_name(field_name: str) -> str:
    """将参数名归一化到可比对形态（取末级字段并去掉数组/占位符符号）。"""
    if not isinstance(field_name, str):
        return ""
    token = field_name.strip()
    if not token:
        return ""
    token = token.split(".")[-1]
    token = token.replace("[]", "")
    token = token.strip("{}")
    return token


def is_generic_param_name(field_name: str) -> bool:
    """判断字段名是否命中通用 ID 参数集合。"""
    normalized = normalize_field_name(field_name)
    if not normalized:
        return False
    # 兼容原规则（大小写敏感集合）与扩展规则（大小写不敏感）
    return (
        normalized in GENERIC_PARAM_NAMES
        or normalized.lower() in {name.lower() for name in GENERIC_PARAM_NAMES}
    )


def load_route_parameters_mapping(project: str, cache_dir: Path) -> dict:
    mapping_file = cache_dir / project / "parameters_dict_all.json"
    route_mapping = {}
    if not mapping_file.exists():
        return route_mapping

    with mapping_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    normalized_data = data.get("normalized_params_process_data", [])
    if not isinstance(normalized_data, list):
        return route_mapping

    for group_item in normalized_data:
        if not isinstance(group_item, dict):
            continue
        group_name = group_item.get("group")
        entries = group_item.get("data", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            route_names = entry.get("route_name", [])
            parameters_name = entry.get("parameters_name")
            if not isinstance(route_names, list) or not isinstance(parameters_name, dict):
                continue
            for route_name in route_names:
                if not isinstance(route_name, str):
                    continue
                # exact key
                route_mapping.setdefault(route_name, []).append(
                    {
                        "group_name": group_name,
                        "parameters_name": parameters_name,
                    }
                )
                # lowercase key for relaxed match
                route_mapping.setdefault(route_name.lower(), []).append(
                    {
                        "group_name": group_name,
                        "parameters_name": parameters_name,
                    }
                )
    return route_mapping


def extract_project_mappings(project: str, cache_dir: Path) -> dict:
    api_doc_path = cache_dir / project / "api_doc_with_type.json"
    mapping_file = cache_dir / project / "parameters_dict_all.json"
    result = {
        "project": project,
        "source_file": str(api_doc_path),
        "mapping_source_file": str(mapping_file),
        "generic_param_names": sorted(GENERIC_PARAM_NAMES),
        "max_extract_count": MAX_PER_PROJECT,
        "extracted": [],
    }

    if not api_doc_path.exists():
        result["error"] = "api_doc_with_type.json not found"
        return result

    with api_doc_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    route_mapping = load_route_parameters_mapping(project, cache_dir)
    # 按接口去重：同一 group/api 只保留一条，汇总请求体/响应体的命中参数
    matches_by_api = {}
    for group_obj in data:
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
                hit_names = set()
                hit_sources = set()
                parameter_in = None

                request_params = api_detail.get("request_parameters", {})
                if isinstance(request_params, dict):
                    for param_name, param_info in request_params.items():
                        if is_generic_param_name(param_name):
                            hit_names.add(param_name)
                            hit_sources.add("request")
                            if parameter_in is None and isinstance(param_info, dict):
                                parameter_in = param_info.get("in")

                response_params = api_detail.get("response_parameters", {})
                if isinstance(response_params, dict):
                    for param_name in response_params.keys():
                        if is_generic_param_name(param_name):
                            hit_names.add(param_name)
                            hit_sources.add("response")

                if not hit_names:
                    continue

                key = (group_name, api_name)
                if key not in matches_by_api:
                    matches_by_api[key] = {
                        "group_name": group_name,
                        "api_name": api_name,
                        "parameter_name": None,  # 向后兼容：保留单值字段
                        "parameter_names": [],
                        "parameter_sources": [],
                        "parameter_in": parameter_in,
                        "mapped_parameters_name": None,
                        "mapped_group_name": None,
                        "mapping_match_type": "unmatched",
                    }

                record = matches_by_api[key]
                merged_names = set(record["parameter_names"])
                merged_names.update(hit_names)
                record["parameter_names"] = sorted(merged_names)
                record["parameter_name"] = record["parameter_names"][0]

                merged_sources = set(record["parameter_sources"])
                merged_sources.update(hit_sources)
                record["parameter_sources"] = sorted(merged_sources)

                if record.get("parameter_in") is None and parameter_in is not None:
                    record["parameter_in"] = parameter_in

    matches = sorted(
        matches_by_api.values(),
        key=lambda x: (x["group_name"], x["api_name"], x["parameter_name"] or ""),
    )
    extracted = matches[:MAX_PER_PROJECT]
    mapped_count = 0
    for item in extracted:
        api_name = item["api_name"]
        candidates = route_mapping.get(api_name)
        match_type = "exact"
        if not candidates:
            candidates = route_mapping.get(api_name.lower())
            match_type = "case_insensitive"
        if candidates:
            first = candidates[0]
            item["mapped_parameters_name"] = first.get("parameters_name")
            item["mapped_group_name"] = first.get("group_name")
            item["mapping_match_type"] = match_type
            mapped_count += 1

    result["extracted"] = extracted
    result["matched_total"] = len(matches)
    result["extracted_count"] = len(result["extracted"])
    result["mapped_count"] = mapped_count
    return result


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_summary_rows(payloads: list[dict], model: str) -> list[dict]:
    rows: list[dict] = []
    for payload in payloads:
        project = payload.get("project", "")
        extracted = payload.get("extracted", [])
        base_info = {
            "model": model,
            "project": project,
            "matched_total": payload.get("matched_total", 0),
            "extracted_count": payload.get("extracted_count", 0),
            "mapped_count": payload.get("mapped_count", 0),
            "error": payload.get("error", ""),
            "source_file": payload.get("source_file", ""),
            "mapping_source_file": payload.get("mapping_source_file", ""),
        }
        if not extracted:
            rows.append(
                {
                    **base_info,
                    "group_name": "",
                    "api_name": "",
                    "parameter_name": "",
                    "parameter_names": "",
                    "parameter_sources": "",
                    "parameter_in": "",
                    "mapped_group_name": "",
                    "mapping_match_type": "",
                    "mapped_parameters_name": "",
                }
            )
            continue

        for item in extracted:
            rows.append(
                {
                    **base_info,
                    "group_name": item.get("group_name", ""),
                    "api_name": item.get("api_name", ""),
                    "parameter_name": item.get("parameter_name", ""),
                    "parameter_names": _safe_json(item.get("parameter_names")),
                    "parameter_sources": _safe_json(item.get("parameter_sources")),
                    "parameter_in": item.get("parameter_in", ""),
                    "mapped_group_name": item.get("mapped_group_name", ""),
                    "mapping_match_type": item.get("mapping_match_type", ""),
                    "mapped_parameters_name": _safe_json(item.get("mapped_parameters_name")),
                }
            )
    return rows


def write_summary_xlsx(xlsx_path: Path, rows: list[dict]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl 依赖，无法写入 xlsx。请先安装: pip install openpyxl") from exc

    headers = [
        "model",
        "project",
        "group_name",
        "api_name",
        "parameter_name",
        "parameter_names",
        "parameter_sources",
        "parameter_in",
        "mapped_group_name",
        "mapping_match_type",
        "mapped_parameters_name",
        "matched_total",
        "extracted_count",
        "mapped_count",
        "error",
        "source_file",
        "mapping_source_file",
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "id_parameter_mappings"
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)


def main() -> int:
    args = parse_args()
    selected_projects = resolve_projects(args.project)
    model_dir = normalize_model_dir(args.model)
    cache_dir = get_cache_dir(args.model)
    output_root_with_model = OUTPUT_ROOT / model_dir
    output_root_with_model.mkdir(parents=True, exist_ok=True)
    all_payloads: list[dict] = []

    if not cache_dir.exists():
        print(f"[warn] cache 目录不存在: {cache_dir}")
        return 0

    for project in selected_projects:
        project_dir = output_root_with_model / project
        project_dir.mkdir(parents=True, exist_ok=True)
        output_file = project_dir / "id_parameter_mappings.json"

        payload = extract_project_mappings(project, cache_dir)
        all_payloads.append(payload)
        with output_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print(
            f"[ok] {project}: extracted {payload.get('extracted_count', 0)} / "
            f"{payload.get('matched_total', 0)} -> {output_file}"
        )

    summary_rows = build_summary_rows(all_payloads, model=model_dir)
    summary_xlsx = output_root_with_model / "id_parameter_mappings_all_projects.xlsx"
    write_summary_xlsx(summary_xlsx, summary_rows)
    print(f"[ok] summary xlsx -> {summary_xlsx}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
