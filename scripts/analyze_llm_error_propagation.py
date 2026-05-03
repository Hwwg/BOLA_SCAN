#!/usr/bin/env python3
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="汇总 LLM 调用审计与关键中间产物，用于误差传播分析"
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="bolascan 根目录",
    )
    parser.add_argument(
        "--cache-roots",
        default="",
        help="要分析的 cache 根目录，逗号分隔；默认扫描 root 下所有 cache_* 目录",
    )
    parser.add_argument(
        "--baseline-cache-root",
        default="",
        help="可选：指定一个 baseline cache 根目录，用于生成 artifact_diff_summary.json",
    )
    parser.add_argument(
        "--out-dir",
        default="scripts/llm_error_propagation",
        help="输出目录；相对 root 或绝对路径",
    )
    return parser.parse_args()


def resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_cache_roots(root: Path, raw: str) -> list[Path]:
    if raw.strip():
        paths = [resolve_path(root, item.strip()) for item in raw.split(",") if item.strip()]
    else:
        paths = sorted(p for p in root.glob("cache_*") if p.is_dir())
    return [p for p in paths if p.exists() and p.is_dir()]


def cache_label(cache_root: Path) -> str:
    name = cache_root.name
    return name[len("cache_") :] if name.startswith("cache_") else name


def iter_project_dirs(cache_root: Path) -> list[Path]:
    return sorted(p for p in cache_root.iterdir() if p.is_dir() and not p.name.startswith("."))


def read_audit_events(project_dir: Path) -> list[dict]:
    audit_path = project_dir / "horizontal_results" / "llm_call_audit.jsonl"
    if not audit_path.exists():
        return []
    events = []
    with audit_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if isinstance(obj, dict):
                events.append(obj)
    return events


def count_api_doc(project_dir: Path) -> dict:
    data = load_json(project_dir / "api_doc_with_type.json")
    total = 0
    tagged = 0
    groups = 0
    if isinstance(data, list):
        for group_obj in data:
            if not isinstance(group_obj, dict):
                continue
            groups += len(group_obj)
            for group_value in group_obj.values():
                if not isinstance(group_value, dict):
                    continue
                for api_name, api_detail in group_value.items():
                    if api_name == "__inherited_params__" or not isinstance(api_detail, dict):
                        continue
                    total += 1
                    if isinstance(api_detail.get("type"), str) and api_detail.get("type", "").strip():
                        tagged += 1
    return {
        "functional_groups": groups,
        "api_total": total,
        "api_type_tagged": tagged,
        "api_type_missing": max(total - tagged, 0),
    }


def count_param_mapping(project_dir: Path) -> dict:
    data = load_json(project_dir / "parameters_dict_all.json")
    entries = 0
    mapped = 0
    route_links = 0
    groups = data.get("normalized_params_process_data", []) if isinstance(data, dict) else []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            for entry in group.get("data", []) or []:
                if not isinstance(entry, dict):
                    continue
                entries += 1
                route_names = entry.get("route_name", [])
                if isinstance(route_names, list):
                    route_links += len(route_names)
                params_name = entry.get("parameters_name", {})
                if isinstance(params_name, dict) and params_name.get("keep_pra"):
                    mapped += 1
    return {
        "param_mapping_entries": entries,
        "param_mapping_mapped": mapped,
        "param_mapping_unmapped": max(entries - mapped, 0),
        "param_mapping_route_links": route_links,
    }


def count_nested_items(obj: Any) -> int:
    if isinstance(obj, list):
        return sum(max(1, count_nested_items(x)) for x in obj) if obj else 0
    if isinstance(obj, dict):
        if any(k in obj for k in ("source", "target", "api", "chain", "dependency")):
            return 1
        return sum(count_nested_items(v) for v in obj.values())
    return 0


def count_dependency_chains(project_dir: Path) -> dict:
    data = load_json(project_dir / "dependency_chains_results.json")
    return {"dependency_chain_items": count_nested_items(data)}


def collect_identifier_names(obj: Any, bucket: set[str]) -> None:
    if isinstance(obj, str):
        text = obj.strip()
        if text:
            bucket.add(text)
    elif isinstance(obj, list):
        for item in obj:
            collect_identifier_names(item, bucket)
    elif isinstance(obj, dict):
        for value in obj.values():
            collect_identifier_names(value, bucket)


def count_identifiers(project_dir: Path) -> dict:
    hr = project_dir / "horizontal_results"
    result: dict[str, int] = {}
    for filename, key in (
        ("data_resource_id_result.json", "data_resource_identifiers"),
        ("container_reoust_id_result.json", "container_identifiers"),
        ("container_resource_divide_results.json", "container_resource_divide_identifiers"),
    ):
        bucket: set[str] = set()
        collect_identifier_names(load_json(hr / filename), bucket)
        result[key] = len(bucket)
    return result


def walk_bola_conclusions(obj: Any, counter: Counter) -> None:
    if isinstance(obj, dict):
        conclusion = obj.get("conclusion")
        if isinstance(conclusion, str):
            counter[conclusion] += 1
        for value in obj.values():
            walk_bola_conclusions(value, counter)
    elif isinstance(obj, list):
        for item in obj:
            walk_bola_conclusions(item, counter)


def count_bola_results(project_dir: Path) -> dict:
    counter: Counter = Counter()
    walk_bola_conclusions(load_json(project_dir / "bola_horizontal_results.json"), counter)
    found = sum(v for k, v in counter.items() if "Found" in k and "Not Found" not in k)
    not_found = sum(v for k, v in counter.items() if "Not Found" in k)
    potential = sum(v for k, v in counter.items() if "Potential" in k)
    return {
        "bola_judgements": sum(counter.values()),
        "bola_found": found,
        "bola_not_found": not_found,
        "bola_potential": potential,
        "bola_conclusion_breakdown": dict(counter),
    }


def artifact_row(cache_root: Path, project_dir: Path) -> dict:
    row = {
        "cache_root": cache_root.name,
        "run_label": cache_label(cache_root),
        "project": project_dir.name,
        "has_api_doc_with_type": int((project_dir / "api_doc_with_type.json").exists()),
        "has_parameters_dict_all": int((project_dir / "parameters_dict_all.json").exists()),
        "has_dependency_chains": int((project_dir / "dependency_chains_results.json").exists()),
        "has_case_packages": int((project_dir / "create_request_data_packages_results.json").exists()),
        "has_execution_results": int((project_dir / "horizontal_results" / "all_acount_execution_results.json").exists()),
        "has_container_divide": int((project_dir / "horizontal_results" / "container_resource_divide_results.json").exists()),
        "has_bola_results": int((project_dir / "bola_horizontal_results.json").exists()),
    }
    row.update(count_api_doc(project_dir))
    row.update(count_param_mapping(project_dir))
    row.update(count_dependency_chains(project_dir))
    row.update(count_identifiers(project_dir))
    row.update(count_bola_results(project_dir))
    return row


def summarize_events(cache_root: Path, project_dir: Path, events: list[dict]) -> tuple[list[dict], dict]:
    buckets: dict[tuple[str, str], dict] = {}
    project_totals = {
        "cache_root": cache_root.name,
        "run_label": cache_label(cache_root),
        "project": project_dir.name,
        "llm_calls": 0,
        "llm_success_calls": 0,
        "llm_failed_calls": 0,
        "llm_recovered_calls": 0,
        "llm_observable_error_events": 0,
        "llm_retry_extra_attempts": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "latency_ms": 0,
    }
    for event in events:
        stage = event.get("stage") or "unknown"
        api = event.get("api") or ""
        key = (stage, api)
        bucket = buckets.setdefault(
            key,
            {
                "cache_root": cache_root.name,
                "run_label": cache_label(cache_root),
                "project": project_dir.name,
                "stage": stage,
                "api": api,
                "llm_calls": 0,
                "success_calls": 0,
                "failed_calls": 0,
                "recovered_calls": 0,
                "observable_error_events": 0,
                "retry_extra_attempts": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "latency_ms": 0,
            },
        )
        attempts = max(int(event.get("attempts") or 0), 0)
        retry_extra = max(attempts - 1, 0)
        outcome = event.get("outcome")
        recovered = outcome == "success" and attempts > 1
        failed = outcome == "failed"
        observable_error = recovered or failed
        for target in (bucket, project_totals):
            call_key = "llm_calls" if target is project_totals else "llm_calls"
            target[call_key] += 1
            if outcome == "success":
                target["llm_success_calls" if target is project_totals else "success_calls"] += 1
            if failed:
                target["llm_failed_calls" if target is project_totals else "failed_calls"] += 1
            if recovered:
                target["llm_recovered_calls" if target is project_totals else "recovered_calls"] += 1
            if observable_error:
                target["llm_observable_error_events" if target is project_totals else "observable_error_events"] += 1
            target["llm_retry_extra_attempts" if target is project_totals else "retry_extra_attempts"] += retry_extra
            target["input_tokens"] += int(event.get("input_tokens") or 0)
            target["output_tokens"] += int(event.get("output_tokens") or 0)
            target["cost"] += float(event.get("cost") or 0.0)
            target["latency_ms"] += int(event.get("latency_ms") or 0)
    stage_rows = list(buckets.values())
    for row in stage_rows:
        calls = row["llm_calls"]
        row["success_rate"] = (row["success_calls"] / calls) if calls else 0.0
        row["recovery_rate"] = (row["recovered_calls"] / row["observable_error_events"]) if row["observable_error_events"] else 0.0
        row["avg_latency_ms"] = (row["latency_ms"] / calls) if calls else 0.0
    calls = project_totals["llm_calls"]
    project_totals["success_rate"] = (project_totals["llm_success_calls"] / calls) if calls else 0.0
    project_totals["recovery_rate"] = (
        project_totals["llm_recovered_calls"] / project_totals["llm_observable_error_events"]
        if project_totals["llm_observable_error_events"]
        else 0.0
    )
    project_totals["avg_latency_ms"] = (project_totals["latency_ms"] / calls) if calls else 0.0
    return stage_rows, project_totals


def build_diff_summary(baseline_root: Path, artifact_rows: list[dict]) -> dict:
    baseline_name = baseline_root.name
    baseline = {
        row["project"]: row
        for row in artifact_rows
        if row.get("cache_root") == baseline_name
    }
    diffs = []
    for row in artifact_rows:
        if row.get("cache_root") == baseline_name:
            continue
        base = baseline.get(row["project"])
        if not base:
            continue
        diff = {
            "baseline_cache_root": baseline_name,
            "compare_cache_root": row["cache_root"],
            "project": row["project"],
        }
        for key in (
            "functional_groups",
            "api_type_tagged",
            "param_mapping_mapped",
            "dependency_chain_items",
            "container_resource_divide_identifiers",
            "bola_judgements",
            "bola_found",
            "bola_potential",
        ):
            diff[f"{key}_baseline"] = base.get(key, 0)
            diff[f"{key}_compare"] = row.get(key, 0)
            diff[f"{key}_delta"] = row.get(key, 0) - base.get(key, 0)
        diffs.append(diff)
    return {"baseline_cache_root": baseline_name, "diffs": diffs}


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    out_dir = resolve_path(root, args.out_dir)
    cache_roots = parse_cache_roots(root, args.cache_roots)

    stage_rows: list[dict] = []
    project_rows: list[dict] = []
    artifact_rows: list[dict] = []
    all_events = 0
    for cache_root in cache_roots:
        for project_dir in iter_project_dirs(cache_root):
            events = read_audit_events(project_dir)
            all_events += len(events)
            rows, project_summary = summarize_events(cache_root, project_dir, events)
            stage_rows.extend(rows)
            project_rows.append(project_summary)
            artifact_rows.append(artifact_row(cache_root, project_dir))

    stage_fields = [
        "cache_root",
        "run_label",
        "project",
        "stage",
        "api",
        "llm_calls",
        "success_calls",
        "failed_calls",
        "recovered_calls",
        "observable_error_events",
        "retry_extra_attempts",
        "success_rate",
        "recovery_rate",
        "input_tokens",
        "output_tokens",
        "cost",
        "avg_latency_ms",
    ]
    project_fields = [
        "cache_root",
        "run_label",
        "project",
        "llm_calls",
        "llm_success_calls",
        "llm_failed_calls",
        "llm_recovered_calls",
        "llm_observable_error_events",
        "llm_retry_extra_attempts",
        "success_rate",
        "recovery_rate",
        "input_tokens",
        "output_tokens",
        "cost",
        "avg_latency_ms",
    ]
    artifact_fields = [
        "cache_root",
        "run_label",
        "project",
        "has_api_doc_with_type",
        "has_parameters_dict_all",
        "has_dependency_chains",
        "has_case_packages",
        "has_execution_results",
        "has_container_divide",
        "has_bola_results",
        "functional_groups",
        "api_total",
        "api_type_tagged",
        "api_type_missing",
        "param_mapping_entries",
        "param_mapping_mapped",
        "param_mapping_unmapped",
        "param_mapping_route_links",
        "dependency_chain_items",
        "data_resource_identifiers",
        "container_identifiers",
        "container_resource_divide_identifiers",
        "bola_judgements",
        "bola_found",
        "bola_not_found",
        "bola_potential",
    ]
    write_csv(out_dir / "llm_stage_summary.csv", stage_rows, stage_fields)
    write_csv(out_dir / "llm_project_summary.csv", project_rows, project_fields)
    write_csv(out_dir / "artifact_stage_summary.csv", artifact_rows, artifact_fields)

    summary = {
        "cache_roots": [str(p) for p in cache_roots],
        "total_audit_events": all_events,
        "projects": len(project_rows),
        "stage_summary_csv": str(out_dir / "llm_stage_summary.csv"),
        "project_summary_csv": str(out_dir / "llm_project_summary.csv"),
        "artifact_summary_csv": str(out_dir / "artifact_stage_summary.csv"),
        "notes": [
            "observable_error_events = final failed calls + calls that succeeded after retry/fallback.",
            "Task-level correctness should be joined with manual oracle outputs from existing eval scripts.",
            "Artifact deltas describe propagation symptoms, not causal proof by themselves.",
        ],
    }
    if args.baseline_cache_root.strip():
        baseline_root = resolve_path(root, args.baseline_cache_root.strip()).resolve()
        diff_summary = build_diff_summary(baseline_root, artifact_rows)
        write_json(out_dir / "artifact_diff_summary.json", diff_summary)
        summary["artifact_diff_summary_json"] = str(out_dir / "artifact_diff_summary.json")
    write_json(out_dir / "llm_error_propagation_summary.json", summary)
    print(f"[done] LLM error propagation summaries written to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
