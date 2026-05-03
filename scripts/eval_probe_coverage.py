#!/usr/bin/env python3
"""Evaluate probe coverage from horizontal execution results.

The script measures whether generated identifier probes actually reached target
APIs. A probe is considered covered when its tested identifier value is non-empty
and the target API has an execution result whose HTTP status is not 401/403.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_BLOCKED_STATUS = {404, 500}
DEFAULT_TARGET_CSV = (
    Path(__file__).resolve().parents[2]
    / "dataset_identifier_parameters"
    / "identifier_parameters_all_projects.csv"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate identifier-value and API coverage from BOLA horizontal probing results."
    )
    parser.add_argument(
        "project_cache",
        help="Project cache directory, e.g. cache_gpt-4o-mini/crapi",
    )
    parser.add_argument(
        "--execution-results",
        default="horizontal_results/all_acount_execution_results.json",
        help="Path to all_acount_execution_results.json, relative to project_cache unless absolute.",
    )
    parser.add_argument(
        "--identifier-results",
        default="horizontal_results/data_resource_id_result.json",
        help="Identifier candidate file, relative to project_cache unless absolute.",
    )
    parser.add_argument(
        "--target-csv",
        default=str(DEFAULT_TARGET_CSV),
        help=(
            "Interface-level ground-truth CSV with columns project-name,interface,parameter. "
            "When present, coverage is computed against these interface-parameter targets."
        ),
    )
    parser.add_argument(
        "--project",
        default="",
        help="Project name in --target-csv. Defaults to the project_cache directory name.",
    )
    parser.add_argument(
        "--out-json",
        default="horizontal_results/probe_coverage_summary.json",
        help="Summary output path, relative to project_cache unless absolute.",
    )
    parser.add_argument(
        "--out-csv",
        default="horizontal_results/probe_coverage_details.csv",
        help="Per-probe output path, relative to project_cache unless absolute.",
    )
    parser.add_argument(
        "--blocked-status",
        default=",".join(str(code) for code in sorted(DEFAULT_BLOCKED_STATUS)),
        help="Comma-separated status codes treated as uncovered probing failures.",
    )
    parser.add_argument(
        "--include-missing-status",
        action="store_true",
        help="Treat executions without a numeric status_code as covered if the target API exists.",
    )
    return parser.parse_args()


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_blocked_status(raw: str) -> set[int]:
    statuses: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        statuses.add(int(part))
    return statuses or set(DEFAULT_BLOCKED_STATUS)


def normalize_identifier_candidates(data: Any) -> dict[str, set[str]]:
    """Return {group_name: {identifier_parameter}} from supported result files."""
    candidates: dict[str, set[str]] = {}
    if isinstance(data, dict) and "resource_id" in data and isinstance(data["resource_id"], list):
        for item in data.get("resource_id", []):
            if isinstance(item, dict):
                for group, params in item.items():
                    if isinstance(params, list):
                        candidates.setdefault(str(group), set()).update(str(p) for p in params if str(p))
        for item in data.get("ou_id", []):
            if isinstance(item, dict):
                for group, params in item.items():
                    if isinstance(params, list):
                        candidates.setdefault(str(group), set()).update(str(p) for p in params if str(p))
        return candidates

    if isinstance(data, dict):
        for group, params in data.items():
            if isinstance(params, list):
                candidates.setdefault(str(group), set()).update(str(p) for p in params if str(p))
    return candidates


def interface_to_api_key(interface: str) -> str:
    text = (interface or "").strip()
    if not text:
        return ""
    if ":" in text and " " not in text.split(":", 1)[0]:
        return text
    parts = text.split(None, 1)
    if len(parts) != 2:
        return text
    return f"{parts[0].upper()}:{parts[1].strip()}"


def load_target_csv(path: Path, project: str) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    if not path.exists():
        return targets
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_project = (row.get("project-name") or row.get("project") or "").strip()
            if project and row_project != project:
                continue
            api_key = interface_to_api_key(row.get("interface") or row.get("api_key") or "")
            parameter = (row.get("parameter") or row.get("param_name") or "").strip()
            if api_key and parameter:
                targets.add((api_key, parameter))
    return targets


def targets_by_api(targets: set[tuple[str, str]]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for api_key, parameter in targets:
        grouped.setdefault(api_key, set()).add(parameter)
    return grouped


def is_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def extract_identifier_values(test_meta: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    param_values = test_meta.get("param_values")
    if isinstance(param_values, dict):
        for location, value in param_values.items():
            if is_non_empty_value(value):
                values.append(
                    {
                        "source": "param_values",
                        "location": location,
                        "name": test_meta.get("param_name"),
                        "value": value,
                    }
                )

    if values:
        return values

    alias_values = test_meta.get("param_alias_values")
    if isinstance(alias_values, dict):
        for name, detail in alias_values.items():
            if isinstance(detail, dict):
                value = detail.get("value")
                if is_non_empty_value(value):
                    values.append(
                        {
                            "source": "param_alias_values",
                            "location": detail.get("position"),
                            "name": name,
                            "value": value,
                        }
                    )
    return values


def assigned_values_for_parameter(
    execution_obj: dict[str, Any],
    parameter: str,
    test_meta: dict[str, Any],
) -> list[Any]:
    """Return non-empty values assigned to a specific interface parameter."""
    values: list[Any] = []
    status = execution_obj.get("execution_status")
    if isinstance(status, dict):
        request_data = status.get("request_data")
        if isinstance(request_data, dict) and is_non_empty_value(request_data.get(parameter)):
            values.append(request_data.get(parameter))

    request_params = execution_obj.get("request_params")
    if isinstance(request_params, dict):
        params = request_params.get("parameters")
        if isinstance(params, dict):
            for location in ("params", "json", "data", "headers"):
                location_values = params.get(location)
                if isinstance(location_values, dict) and is_non_empty_value(location_values.get(parameter)):
                    values.append(location_values.get(parameter))

    if str(test_meta.get("param_name") or "") == parameter:
        for item in extract_identifier_values(test_meta):
            if is_non_empty_value(item.get("value")):
                values.append(item.get("value"))

    alias_values = test_meta.get("param_alias_values")
    if isinstance(alias_values, dict):
        detail = alias_values.get(parameter)
        if isinstance(detail, dict) and is_non_empty_value(detail.get("value")):
            values.append(detail.get("value"))

    unique_values: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def iter_execution_objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        execution_status = node.get("execution_status")
        if isinstance(execution_status, dict):
            found.append(node)
        for value in node.values():
            found.extend(iter_execution_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(iter_execution_objects(item))
    return found


def execution_status_code(execution_obj: dict[str, Any]) -> int | None:
    status = execution_obj.get("execution_status")
    if not isinstance(status, dict):
        return None
    code = status.get("status_code")
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.isdigit():
        return int(code)
    response = execution_obj.get("response_params")
    if isinstance(response, dict):
        params = response.get("parameters")
        if isinstance(params, dict):
            nested_code = params.get("status")
            if isinstance(nested_code, int):
                return nested_code
            if isinstance(nested_code, str) and nested_code.isdigit():
                return int(nested_code)
    return None


def execution_api_key(execution_obj: dict[str, Any]) -> str | None:
    status = execution_obj.get("execution_status")
    if isinstance(status, dict) and status.get("api_key"):
        return str(status["api_key"])
    method = execution_obj.get("method")
    route = execution_obj.get("route")
    if method and route:
        return f"{method}:{route}"
    return None


def target_api_keys_from_meta(test_meta: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    param_source_path = test_meta.get("param_source_path")
    if isinstance(param_source_path, dict):
        for account_detail in param_source_path.values():
            if isinstance(account_detail, dict) and account_detail.get("api_key"):
                keys.add(str(account_detail["api_key"]))
    if not keys:
        strategy_key = test_meta.get("api_key")
        if strategy_key:
            keys.add(str(strategy_key))
    return keys


def status_is_covered(
    status_code: int | None,
    blocked_status: set[int],
    include_missing_status: bool,
) -> bool:
    if status_code is None:
        return include_missing_status
    return status_code not in blocked_status


def evaluate_ground_truth_coverage(
    project_cache: Path,
    execution_data: Any,
    target_interface_params: set[tuple[str, str]],
    target_csv_path: Path,
    project_name: str,
    blocked_status: set[int],
    include_missing_status: bool,
    execution_path: Path,
    identifier_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_api = targets_by_api(target_interface_params)
    target_api_universe = set(by_api)
    attempted_interface_params: set[tuple[str, str]] = set()
    covered_interface_params: set[tuple[str, str]] = set()
    covered_api_keys: set[str] = set()
    blocked_api_keys: set[str] = set()
    total_probe_cases = 0
    probe_cases_with_values = 0
    covered_probe_cases = 0
    status_counter: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    for category, groups in (execution_data or {}).items():
        if not isinstance(groups, dict):
            continue
        for group_name, params in groups.items():
            if not isinstance(params, dict):
                continue
            for grouped_param_name, buckets in params.items():
                if not isinstance(buckets, dict):
                    continue
                for bucket_name, cases in buckets.items():
                    if not isinstance(cases, list):
                        continue
                    for case_index, case in enumerate(cases):
                        if not isinstance(case, dict):
                            continue
                        total_probe_cases += 1
                        test_meta = case.get("test_meta") if isinstance(case.get("test_meta"), dict) else {}
                        case_has_value = False
                        case_covered = False
                        for execution_obj in iter_execution_objects(case):
                            api_key = execution_api_key(execution_obj)
                            if not api_key or api_key not in by_api:
                                continue
                            status_code = execution_status_code(execution_obj)
                            status_counter[str(status_code)] = status_counter.get(str(status_code), 0) + 1
                            status_covered = status_is_covered(
                                status_code,
                                blocked_status,
                                include_missing_status,
                            )
                            for target_parameter in sorted(by_api[api_key]):
                                assigned_values = assigned_values_for_parameter(
                                    execution_obj,
                                    target_parameter,
                                    test_meta,
                                )
                                if not assigned_values:
                                    continue
                                case_has_value = True
                                attempted_interface_params.add((api_key, target_parameter))
                                blocked_by_auth = status_code in blocked_status
                                if blocked_by_auth:
                                    blocked_api_keys.add(api_key)
                                if status_covered:
                                    case_covered = True
                                    covered_api_keys.add(api_key)
                                    covered_interface_params.add((api_key, target_parameter))
                                rows.append(
                                    {
                                        "project_name": project_name,
                                        "category": category,
                                        "group_name": group_name,
                                        "grouped_param_name": grouped_param_name,
                                        "target_parameter": target_parameter,
                                        "bucket": bucket_name,
                                        "case_index": case_index,
                                        "has_identifier_value": True,
                                        "identifier_value_count": len(assigned_values),
                                        "identifier_values": json.dumps(assigned_values, ensure_ascii=False),
                                        "target_api_keys": api_key,
                                        "observed_api_keys": api_key,
                                        "observed_status_codes": "" if status_code is None else str(status_code),
                                        "blocked_by_auth_status": blocked_by_auth,
                                        "covered": bool(status_covered),
                                    }
                                )
                        if case_has_value:
                            probe_cases_with_values += 1
                        if case_has_value and case_covered:
                            covered_probe_cases += 1

    summary = {
        "project_cache": str(project_cache),
        "project_name": project_name,
        "execution_results": str(execution_path),
        "identifier_results": str(identifier_path),
        "target_csv": str(target_csv_path),
        "blocked_status": sorted(blocked_status),
        "target_interface_parameters": len(target_interface_params),
        "attempted_interface_parameters": len(attempted_interface_params),
        "covered_interface_parameters": len(covered_interface_params),
        "interface_parameter_coverage": safe_div(
            len(covered_interface_params),
            len(target_interface_params),
        ),
        "total_probe_cases": total_probe_cases,
        "probe_cases_with_identifier_values": probe_cases_with_values,
        "covered_probe_cases": covered_probe_cases,
        "probe_case_coverage": safe_div(covered_probe_cases, probe_cases_with_values),
        "target_api_interfaces": len(target_api_universe),
        "covered_api_interfaces": len(covered_api_keys),
        "api_interface_coverage": safe_div(len(covered_api_keys), len(target_api_universe)),
        "auth_blocked_api_interfaces": len(blocked_api_keys),
        "covered_api_keys": sorted(covered_api_keys),
        "uncovered_api_keys": sorted(target_api_universe - covered_api_keys),
        "covered_interface_parameter_keys": [
            {"api_key": api_key, "parameter": parameter}
            for api_key, parameter in sorted(covered_interface_params)
        ],
        "uncovered_interface_parameter_keys": [
            {"api_key": api_key, "parameter": parameter}
            for api_key, parameter in sorted(target_interface_params - covered_interface_params)
        ],
        "status_counter": dict(sorted(status_counter.items(), key=lambda item: item[0])),
    }
    return summary, rows


def evaluate(project_cache: Path, args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    execution_path = resolve_path(project_cache, args.execution_results)
    identifier_path = resolve_path(project_cache, args.identifier_results)
    target_csv_path = resolve_path(Path.cwd(), args.target_csv)
    project_name = args.project.strip() or project_cache.name
    blocked_status = parse_blocked_status(args.blocked_status)

    execution_data = load_json(execution_path)
    identifiers = normalize_identifier_candidates(load_json(identifier_path))
    target_interface_params = load_target_csv(target_csv_path, project_name)
    if target_interface_params:
        return evaluate_ground_truth_coverage(
            project_cache=project_cache,
            execution_data=execution_data,
            target_interface_params=target_interface_params,
            target_csv_path=target_csv_path,
            project_name=project_name,
            blocked_status=blocked_status,
            include_missing_status=args.include_missing_status,
            execution_path=execution_path,
            identifier_path=identifier_path,
        )
    target_api_universe = {api_key for api_key, _ in target_interface_params}

    candidate_pairs = {
        (group, param)
        for group, params in identifiers.items()
        for param in params
        if group and param
    }
    all_target_api_keys: set[str] = set(target_api_universe)
    covered_api_keys: set[str] = set()
    blocked_api_keys: set[str] = set()
    attempted_interface_params: set[tuple[str, str]] = set()
    covered_interface_params: set[tuple[str, str]] = set()
    covered_group_params: set[tuple[str, str]] = set()
    attempted_group_params: set[tuple[str, str]] = set()
    total_probe_cases = 0
    probe_cases_with_values = 0
    covered_probe_cases = 0
    total_identifier_value_occurrences = 0
    covered_identifier_value_occurrences = 0
    status_counter: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    for category, groups in (execution_data or {}).items():
        if not isinstance(groups, dict):
            continue
        for group_name, params in groups.items():
            if not isinstance(params, dict):
                continue
            for param_name, buckets in params.items():
                group_param = (str(group_name), str(param_name))
                if not target_interface_params and candidate_pairs and group_param not in candidate_pairs:
                    continue
                if not isinstance(buckets, dict):
                    continue
                for bucket_name, cases in buckets.items():
                    if not isinstance(cases, list):
                        continue
                    for case_index, case in enumerate(cases):
                        if not isinstance(case, dict):
                            continue
                        total_probe_cases += 1
                        test_meta = case.get("test_meta") if isinstance(case.get("test_meta"), dict) else {}
                        identifier_values = extract_identifier_values(test_meta)
                        target_keys = target_api_keys_from_meta(test_meta)
                        target_param_name = str(test_meta.get("param_name") or param_name)
                        matching_interface_params = {
                            (api_key, target_param_name)
                            for api_key in target_keys
                            if (api_key, target_param_name) in target_interface_params
                        }
                        if target_interface_params and not matching_interface_params:
                            continue
                        executions = iter_execution_objects(case)
                        target_executions = []
                        for execution_obj in executions:
                            api_key = execution_api_key(execution_obj)
                            if target_keys and api_key not in target_keys:
                                continue
                            if api_key:
                                target_executions.append(execution_obj)

                        if not target_executions and not target_keys:
                            target_executions = executions

                        observed_statuses: list[int | None] = []
                        observed_api_keys: set[str] = set()
                        case_covered = False
                        case_blocked = False
                        for execution_obj in target_executions:
                            api_key = execution_api_key(execution_obj)
                            status_code = execution_status_code(execution_obj)
                            observed_statuses.append(status_code)
                            if api_key:
                                observed_api_keys.add(api_key)
                                all_target_api_keys.add(api_key)
                            status_counter[str(status_code)] = status_counter.get(str(status_code), 0) + 1
                            if status_code in blocked_status:
                                case_blocked = True
                                if api_key:
                                    blocked_api_keys.add(api_key)
                            if status_is_covered(status_code, blocked_status, args.include_missing_status):
                                case_covered = True
                                if api_key:
                                    covered_api_keys.add(api_key)

                        if identifier_values:
                            probe_cases_with_values += 1
                            if matching_interface_params:
                                attempted_interface_params.update(matching_interface_params)
                            else:
                                attempted_group_params.add(group_param)
                            total_identifier_value_occurrences += len(identifier_values)
                            for key in target_keys:
                                all_target_api_keys.add(key)

                        if identifier_values and case_covered:
                            covered_probe_cases += 1
                            if matching_interface_params:
                                covered_interface_params.update(matching_interface_params)
                            else:
                                covered_group_params.add(group_param)
                            covered_identifier_value_occurrences += len(identifier_values)

                        rows.append(
                            {
                                "category": category,
                                "group_name": group_name,
                                "param_name": param_name,
                                "bucket": bucket_name,
                                "case_index": case_index,
                                "has_identifier_value": bool(identifier_values),
                                "identifier_value_count": len(identifier_values),
                                "identifier_values": json.dumps(identifier_values, ensure_ascii=False),
                                "target_api_keys": ";".join(sorted(target_keys or observed_api_keys)),
                                "observed_api_keys": ";".join(sorted(observed_api_keys)),
                                "observed_status_codes": ";".join("" if s is None else str(s) for s in observed_statuses),
                                "blocked_by_auth_status": case_blocked,
                                "covered": bool(identifier_values and case_covered),
                            }
                        )

    summary = {
        "project_cache": str(project_cache),
        "project_name": project_name,
        "execution_results": str(execution_path),
        "identifier_results": str(identifier_path),
        "target_csv": str(target_csv_path) if target_csv_path.exists() else "",
        "blocked_status": sorted(blocked_status),
        "target_interface_parameters": len(target_interface_params),
        "attempted_interface_parameters": len(attempted_interface_params),
        "covered_interface_parameters": len(covered_interface_params),
        "interface_parameter_coverage": safe_div(
            len(covered_interface_params),
            len(target_interface_params),
        ),
        "candidate_identifier_parameters": len(candidate_pairs),
        "attempted_identifier_parameters": len(attempted_group_params),
        "covered_identifier_parameters": len(covered_group_params),
        "identifier_parameter_coverage": safe_div(len(covered_group_params), len(candidate_pairs)),
        "total_probe_cases": total_probe_cases,
        "probe_cases_with_identifier_values": probe_cases_with_values,
        "covered_probe_cases": covered_probe_cases,
        "probe_case_coverage": safe_div(covered_probe_cases, probe_cases_with_values),
        "target_api_interfaces": len(all_target_api_keys),
        "covered_api_interfaces": len(covered_api_keys),
        "api_interface_coverage": safe_div(len(covered_api_keys), len(all_target_api_keys)),
        "auth_blocked_api_interfaces": len(blocked_api_keys),
        "covered_api_keys": sorted(covered_api_keys),
        "uncovered_api_keys": sorted(all_target_api_keys - covered_api_keys),
        "status_counter": dict(sorted(status_counter.items(), key=lambda item: item[0])),
    }
    return summary, rows


def safe_div(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def write_outputs(project_cache: Path, args: argparse.Namespace, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    out_json = resolve_path(project_cache, args.out_json)
    out_csv = resolve_path(project_cache, args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    fieldnames = [
        "project_name",
        "category",
        "group_name",
        "grouped_param_name",
        "target_parameter",
        "param_name",
        "bucket",
        "case_index",
        "has_identifier_value",
        "identifier_value_count",
        "identifier_values",
        "target_api_keys",
        "observed_api_keys",
        "observed_status_codes",
        "blocked_by_auth_status",
        "covered",
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[ok] summary -> {out_json}")
    print(f"[ok] details -> {out_csv}")
    print(
        "[summary] "
        f"api={summary['covered_api_interfaces']}/{summary['target_api_interfaces']} "
        f"({summary['api_interface_coverage']:.2%}), "
        f"interface_params={summary['covered_interface_parameters']}/"
        f"{summary['target_interface_parameters']} "
        f"({summary['interface_parameter_coverage']:.2%})"
    )


def main() -> int:
    args = parse_args()
    project_cache = Path(args.project_cache).resolve()
    summary, rows = evaluate(project_cache, args)
    write_outputs(project_cache, args, summary, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
