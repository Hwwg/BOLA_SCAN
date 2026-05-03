#!/usr/bin/env python3
import argparse
import ast
import csv
import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDENTIFIER_GOLD = (
    DEFAULT_ROOT.parent
    / "dataset_identifier_parameters"
    / "identifier_parameters_unique_project_parameter.csv"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default="gpt-4o-mini,gpt-5-mini,deepseek-chat,qwen3.6-flash-2026-04-16,gemini-2.5-flash-preview-09-2025",
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--gold-groups",
        default="scripts/manual_label_api_group_ground_truth.csv",
    )
    parser.add_argument(
        "--gold-type",
        default="scripts/manual_label_api_type_ground_truth.csv",
    )
    parser.add_argument(
        "--gold-mapping",
        default="scripts/manual_label_param_mapping_ground_truth.csv",
    )
    parser.add_argument(
        "--gold-identifiers",
        default=str(DEFAULT_IDENTIFIER_GOLD),
    )
    parser.add_argument(
        "--gold-cads",
        default="scripts/manual_label_cads_dependency_pairs_ground_truth.csv",
    )
    parser.add_argument("--mapping-topk", type=int, default=3)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--debug-out-dir",
        default="scripts/eval_debug_cases",
    )
    parser.add_argument(
        "--out-summary-csv",
        default="scripts/eval_all_experiments_summary.csv",
    )
    parser.add_argument(
        "--out-summary-md",
        default="scripts/eval_all_experiments_summary.md",
    )
    parser.add_argument(
        "--out-overview-csv",
        default="scripts/eval_all_experiments_overview.csv",
    )
    parser.add_argument(
        "--out-identifier-gt-report-csv",
        default="scripts/identifier_ground_truth_hit_report.csv",
    )
    parser.add_argument(
        "--out-identifier-gt-missed-all-csv",
        default="scripts/identifier_ground_truth_missed_all_models.csv",
    )
    parser.add_argument(
        "--out-identifier12-summary-csv",
        default="scripts/eval_identifier12_summary.csv",
    )
    parser.add_argument(
        "--out-overview-md",
        default="",
    )
    parser.add_argument(
        "--out-overview-html",
        default="scripts/eval_all_experiments_overview.html",
    )
    parser.add_argument("--print-overview-table", action="store_true")
    parser.add_argument(
        "--out-json",
        default="scripts/eval_all_experiments_result.json",
    )
    parser.add_argument("--no-version-drift-plot", action="store_true")
    parser.add_argument("--benchmark-prec", default="")
    parser.add_argument("--benchmark-acc", default="79.31,80.00,80.00,80.00,80.00")
    parser.add_argument("--benchmark-rec", default="69.70,72.73,72.73,72.73,72.73")
    parser.add_argument("--testset-csv", default="../实验数据.csv")
    parser.add_argument("--testset-fp-p", type=float, default=0.6)
    parser.add_argument("--testset-mode", choices=["union_cum", "per_run"], default="union_cum")
    return parser.parse_args()


def parse_list_field(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            data = parser(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return [text]


def load_gold_groups(csv_fp: Path) -> dict[str, dict[str, str]]:
    gold: dict[str, dict[str, str]] = {}
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            project = (r.get("project") or "").strip()
            api_name = (r.get("api_name") or "").strip()
            group = (r.get("gold_group") or "").strip()
            if not project or not api_name or not group:
                continue
            gold.setdefault(project, {})[api_name] = group
    return gold


def load_gold_type(csv_fp: Path) -> list[dict]:
    rows = []
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "project": (r.get("project") or "").strip(),
                    "api_name": (r.get("api_name") or "").strip(),
                    "gold_type": (r.get("gold_type") or "").strip(),
                }
            )
    return rows


def load_gold_mapping(csv_fp: Path) -> list[dict]:
    rows = []
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "project": (r.get("project") or "").strip(),
                    "api_name": (r.get("api_name") or "").strip(),
                    "parameter_name": (r.get("parameter_name") or "").strip(),
                    "gold_replace_para": parse_list_field(r.get("gold_replace_para") or ""),
                    "gold_keep_pra": parse_list_field(r.get("gold_keep_pra") or ""),
                }
            )
    return rows


def load_gold_identifier_dataset(csv_fp: Path) -> list[dict]:
    rows = []
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = {str(x or "").strip() for x in (reader.fieldnames or [])}
        project_key = "project-name" if "project-name" in fieldnames else "project"
        label_key = "类型" if "类型" in fieldnames else "type"
        for r in reader:
            project = (r.get(project_key) or "").strip()
            parameter = (r.get("parameter") or "").strip()
            raw = (r.get(label_key) or "").strip()
            if not project or not parameter or not raw:
                continue
            try:
                label = int(raw)
            except Exception:
                continue
            rows.append({"project": project, "parameter": parameter, "label": label})
    return rows


def load_gold_cads_pairs(csv_fp: Path) -> list[dict]:
    if not csv_fp.exists():
        return []
    rows: list[dict] = []
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            project = (r.get("project") or "").strip()
            upstream = (r.get("upstream_api") or "").strip()
            downstream = (r.get("downstream_api") or "").strip()
            if not project or not upstream or not downstream:
                continue
            rows.append(
                {
                    "project": project,
                    "upstream_api": upstream,
                    "downstream_api": downstream,
                }
            )
    return rows


def load_type_pred(cache_root: Path) -> dict[tuple[str, str], str]:
    pred: dict[tuple[str, str], str] = {}
    if not cache_root.exists():
        return pred
    for project_dir in cache_root.iterdir():
        if not project_dir.is_dir():
            continue
        fp = project_dir / "api_doc_with_type.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for group_obj in data:
            if not isinstance(group_obj, dict):
                continue
            for _, group_value in group_obj.items():
                if not isinstance(group_value, dict):
                    continue
                for api_name, api_detail in group_value.items():
                    if api_name == "__inherited_params__":
                        continue
                    if not isinstance(api_name, str) or not isinstance(api_detail, dict):
                        continue
                    t = api_detail.get("type")
                    if isinstance(t, str) and t.strip():
                        pred[(project_dir.name, api_name.strip())] = t.strip()
    return pred


def load_mapping_pred(cache_root: Path) -> dict[tuple[str, str], list[dict]]:
    pred: dict[tuple[str, str], list[dict]] = {}
    if not cache_root.exists():
        return pred
    for project_dir in cache_root.iterdir():
        if not project_dir.is_dir():
            continue
        fp = project_dir / "parameters_dict_all.json"
        if not fp.exists():
            continue
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        groups = obj.get("normalized_params_process_data", []) if isinstance(obj, dict) else []
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            entries = group.get("data", [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                route_names = entry.get("route_name", [])
                params_name = entry.get("parameters_name", {})
                if not isinstance(route_names, list) or not isinstance(params_name, dict):
                    continue
                keep_pra = params_name.get("keep_pra")
                replace_para = params_name.get("replace_para", [])
                keep_pra = str(keep_pra).strip() if keep_pra is not None else ""
                replace_para = (
                    [str(x).strip() for x in replace_para if str(x).strip()]
                    if isinstance(replace_para, list)
                    else []
                )
                cand = {"keep_pra": keep_pra, "replace_para": replace_para}
                for route in route_names:
                    if isinstance(route, str) and route.strip():
                        key = (project_dir.name, route.strip())
                        pred.setdefault(key, []).append(cand)
    return pred


def add_from_obj(bucket: set[str], obj) -> None:
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            bucket.add(s)
        return
    if isinstance(obj, list):
        for x in obj:
            add_from_obj(bucket, x)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            add_from_obj(bucket, v)


def load_identifier_pred(cache_root: Path) -> dict[str, dict[str, set[str]]]:
    pred: dict[str, dict[str, set[str]]] = {}
    if not cache_root.exists():
        return pred

    for project_dir in cache_root.iterdir():
        if not project_dir.is_dir():
            continue
        hr = project_dir / "horizontal_results"
        regular: set[str] = set()
        container: set[str] = set()

        dr = hr / "data_resource_id_result.json"
        if dr.exists():
            try:
                add_from_obj(regular, json.loads(dr.read_text(encoding="utf-8")))
            except Exception:
                pass

        cr = hr / "container_reoust_id_result.json"
        if cr.exists():
            try:
                add_from_obj(container, json.loads(cr.read_text(encoding="utf-8")))
            except Exception:
                pass

        divide = hr / "container_resource_divide_results.json"
        if divide.exists():
            try:
                obj = json.loads(divide.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    add_from_obj(container, obj.get("ou_id"))
                    add_from_obj(regular, obj.get("resource_id"))
            except Exception:
                pass

        pred[project_dir.name] = {"regular": regular, "container": container}
    return pred


def compute_pairwise(api_to_gold: dict[str, str], api_to_pred: dict[str, str]) -> dict:
    apis = sorted(api_to_gold.keys())
    if len(apis) < 2:
        return {
            "apis": len(apis),
            "pairs": 0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
        }

    tp = fp = fn = 0
    for a, b in combinations(apis, 2):
        gold_same = api_to_gold.get(a) == api_to_gold.get(b)
        pred_same = api_to_pred.get(a, f"__missing__:{a}") == api_to_pred.get(
            b, f"__missing__:{b}"
        )
        if gold_same and pred_same:
            tp += 1
        elif (not gold_same) and pred_same:
            fp += 1
        elif gold_same and (not pred_same):
            fn += 1

    precision = (tp / (tp + fp)) if (tp + fp) else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "apis": len(apis),
        "pairs": len(apis) * (len(apis) - 1) // 2,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def compute_prf(tp: int, fp: int, fn: int) -> dict:
    precision = (tp / (tp + fp)) if (tp + fp) else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def write_csv(fp: Path, fieldnames: list[str], rows: list[dict]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def format_pct(value: float) -> str:
    try:
        return f"{value * 100:.2f}%"
    except Exception:
        return "0.00%"


def to_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def to_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def safe_f1(precision: float, recall: float) -> float:
    if precision <= 0.0 or recall <= 0.0:
        return 0.0
    denom = precision + recall
    return (2 * precision * recall / denom) if denom else 0.0


def write_markdown_table(fp: Path, headers: list[str], rows: list[list[str]]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    lines = [head, sep]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(fp: Path, html: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(html, encoding="utf-8")


def normalize_model_dir(model: str) -> str:
    value = (model or "").strip()
    if not value:
        return "default"
    return value.replace("/", "_")


def resolve_cache_dir(root: Path, model: str) -> Path:
    token = normalize_model_dir(model)
    direct = root / token
    if direct.exists() and direct.is_dir():
        return direct
    if token.startswith("cache_"):
        direct_cache = root / token
        if direct_cache.exists() and direct_cache.is_dir():
            return direct_cache
        token = token[len("cache_") :]
    return root / f"cache_{token}"


def avg(values: list[float]) -> float:
    usable = [v for v in values if isinstance(v, (int, float))]
    return (sum(usable) / len(usable)) if usable else 0.0


def format_num(value: float) -> str:
    if value is None:
        return "-"
    try:
        f = float(value)
    except Exception:
        return "-"
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.2f}"

def write_overview_csv(fp: Path, rows: list[list[str]]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    header_row_1 = [
        "model",
        "functional_group",
        "functional_group",
        "functional_group",
        "functional_group",
        "parameter_mapping",
        "parameter_mapping",
        "parameter_mapping",
        "parameter_mapping",
        "operation_semantics",
        "operation_semantics",
        "operation_semantics",
        "identifier_recognition",
        "identifier_recognition",
        "identifier_recognition",
        "identifier_recognition",
        "identifier_recognition",
        "cads_dependency",
        "cads_dependency",
        "cads_dependency",
        "cads_dependency",
        "overall",
        "overall",
    ]
    header_row_2 = [
        "model",
        "tp",
        "fp",
        "prec",
        "rec",
        "tp",
        "fp",
        "prec",
        "rec",
        "tp",
        "fp",
        "prec",
        "tp",
        "fp",
        "prec",
        "rec",
        "missed_fn_label3_ratio",
        "tp",
        "fp",
        "prec",
        "rec",
        "avg_prec",
        "avg_rec",
    ]
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header_row_1)
        w.writerow(header_row_2)
        w.writerows(rows)

def pattern_key(mask: int, model_count: int) -> str:
    return bin(mask)[2:].zfill(model_count)


def mask_to_models(mask: int, models: list[str]) -> list[str]:
    hit: list[str] = []
    for idx, m in enumerate(models):
        if mask & (1 << idx):
            hit.append(m)
    return hit


def eval_type(gold_rows: list[dict], pred_rows: dict[tuple[str, str], str]) -> dict:
    total = len(gold_rows)
    found = 0
    correct = 0
    labels: set[str] = set()
    tp_by: dict[str, int] = {}
    fp_by: dict[str, int] = {}
    fn_by: dict[str, int] = {}
    for row in gold_rows:
        key = (row["project"], row["api_name"])
        gold = row["gold_type"]
        labels.add(gold)
        pred = pred_rows.get(key)
        if pred is None:
            continue
        found += 1
        if pred == gold:
            correct += 1
            tp_by[gold] = tp_by.get(gold, 0) + 1
            continue
        fp_by[pred] = fp_by.get(pred, 0) + 1
        fn_by[gold] = fn_by.get(gold, 0) + 1

    labels_list = sorted(labels)
    macro_prec_items: list[float] = []
    macro_rec_items: list[float] = []
    for c in labels_list:
        tp = tp_by.get(c, 0)
        fp = fp_by.get(c, 0)
        fn = fn_by.get(c, 0)
        prec = (tp / (tp + fp)) if (tp + fp) else 0.0
        rec = (tp / (tp + fn)) if (tp + fn) else 0.0
        macro_prec_items.append(prec)
        macro_rec_items.append(rec)

    macro_precision = (sum(macro_prec_items) / len(macro_prec_items)) if macro_prec_items else 0.0
    macro_recall = (sum(macro_rec_items) / len(macro_rec_items)) if macro_rec_items else 0.0
    macro_f1 = (
        (2 * macro_precision * macro_recall / (macro_precision + macro_recall))
        if (macro_precision + macro_recall)
        else 0.0
    )
    accuracy = (correct / total) if total else 0.0
    return {
        "total": total,
        "found": found,
        "correct": correct,
        "precision": macro_precision,
        "recall": macro_recall,
        "f1": macro_f1,
        "accuracy": accuracy,
    }


def collect_type_mismatches(gold_rows: list[dict], pred_rows: dict[tuple[str, str], str]) -> list[dict]:
    rows: list[dict] = []
    for row in gold_rows:
        project = row["project"]
        api_name = row["api_name"]
        gold_type = row["gold_type"]
        pred_type = pred_rows.get((project, api_name))
        if pred_type is None:
            rows.append(
                {
                    "project": project,
                    "api_name": api_name,
                    "gold_type": gold_type,
                    "pred_type": "",
                    "error_type": "missing",
                }
            )
            continue
        if pred_type != gold_type:
            rows.append(
                {
                    "project": project,
                    "api_name": api_name,
                    "gold_type": gold_type,
                    "pred_type": pred_type,
                    "error_type": "mismatch",
                }
            )
    return rows


def eval_mapping_topk(gold_rows: list[dict], pred_rows: dict[tuple[str, str], list[dict]], topk: int) -> dict:
    total = len(gold_rows)
    found = 0
    correct = 0
    for row in gold_rows:
        key = (row["project"], row["api_name"])
        candidates = pred_rows.get(key, [])
        if not candidates:
            continue
        found += 1
        gold_keep = {x.strip() for x in row["gold_keep_pra"] if x and x.strip()}
        gold_replace = {x.strip() for x in row["gold_replace_para"] if x and x.strip()}
        ok = False
        for cand in candidates[: max(1, topk)]:
            cand_keep = (cand.get("keep_pra") or "").strip()
            cand_replace = {x.strip() for x in (cand.get("replace_para") or []) if x and x.strip()}
            if cand_replace & gold_replace and ((not gold_keep) or cand_keep in gold_keep):
                ok = True
                break
        if ok:
            correct += 1

    precision = (correct / found) if found else 0.0
    recall = (correct / total) if total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "total": total,
        "found": found,
        "correct": correct,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "topk": topk,
    }


def collect_mapping_mismatches(
    gold_rows: list[dict],
    pred_rows: dict[tuple[str, str], list[dict]],
    topk: int,
) -> list[dict]:
    rows: list[dict] = []
    for row in gold_rows:
        project = row["project"]
        api_name = row["api_name"]
        parameter_name = row.get("parameter_name", "")
        gold_keep = [x.strip() for x in row["gold_keep_pra"] if x and x.strip()]
        gold_replace = [x.strip() for x in row["gold_replace_para"] if x and x.strip()]
        candidates = pred_rows.get((project, api_name), [])
        if not candidates:
            rows.append(
                {
                    "project": project,
                    "api_name": api_name,
                    "parameter_name": parameter_name,
                    "gold_keep_pra": json.dumps(gold_keep, ensure_ascii=False),
                    "gold_replace_para": json.dumps(gold_replace, ensure_ascii=False),
                    "pred_topk": "[]",
                    "error_type": "missing",
                }
            )
            continue

        gold_keep_set = set(gold_keep)
        gold_replace_set = set(gold_replace)
        ok = False
        top = candidates[: max(1, int(topk))]
        for cand in top:
            cand_keep = (cand.get("keep_pra") or "").strip()
            cand_replace = {x.strip() for x in (cand.get("replace_para") or []) if x and x.strip()}
            if cand_replace & gold_replace_set and ((not gold_keep_set) or cand_keep in gold_keep_set):
                ok = True
                break
        if ok:
            continue
        rows.append(
            {
                "project": project,
                "api_name": api_name,
                "parameter_name": parameter_name,
                "gold_keep_pra": json.dumps(gold_keep, ensure_ascii=False),
                "gold_replace_para": json.dumps(gold_replace, ensure_ascii=False),
                "pred_topk": json.dumps(top, ensure_ascii=False),
                "error_type": "mismatch",
            }
        )
    return rows


def eval_identifiers(
    gold_rows: list[dict], pred_rows: dict[str, dict[str, set[str]]]
) -> dict:
    overall_tp = overall_fp = overall_fn = 0
    regular_tp = regular_fp = regular_fn = 0
    container_tp = container_fp = container_fn = 0

    for row in gold_rows:
        project = row["project"]
        parameter = row["parameter"]
        label = int(row["label"])

        preds = pred_rows.get(project, {"regular": set(), "container": set()})
        pred_regular = parameter in preds.get("regular", set())
        pred_container = parameter in preds.get("container", set())
        pred_any = pred_regular or pred_container

        gold_regular = label in {1, 3}
        gold_container = label == 2
        gold_any = gold_regular or gold_container

        if gold_any and pred_any:
            overall_tp += 1
        elif (not gold_any) and pred_any:
            overall_fp += 1
        elif gold_any and (not pred_any):
            overall_fn += 1

        if label != 2:
            if gold_regular and pred_regular:
                regular_tp += 1
            elif (not gold_regular) and pred_regular:
                regular_fp += 1
            elif gold_regular and (not pred_regular):
                regular_fn += 1

        if gold_container and pred_container:
            container_tp += 1
        elif (not gold_container) and pred_container:
            container_fp += 1
        elif gold_container and (not pred_container):
            container_fn += 1

    return {
        "overall": compute_prf(overall_tp, overall_fp, overall_fn),
        "regular": compute_prf(regular_tp, regular_fp, regular_fn),
        "container": compute_prf(container_tp, container_fp, container_fn),
    }


def eval_identifiers_subset(
    gold_rows: list[dict],
    pred_rows: dict[str, dict[str, set[str]]],
    positive_labels: set[int],
    ignore_labels: set[int],
) -> dict:
    tp = fp = fn = 0
    per_label_total: dict[int, int] = {l: 0 for l in positive_labels}
    per_label_hit: dict[int, int] = {l: 0 for l in positive_labels}
    for row in gold_rows:
        project = row["project"]
        parameter = row["parameter"]
        label = int(row["label"])
        if label in ignore_labels:
            continue
        preds = pred_rows.get(project, {"regular": set(), "container": set()})
        pred_any = (parameter in preds.get("regular", set())) or (parameter in preds.get("container", set()))
        gold_pos = label in positive_labels
        if gold_pos:
            per_label_total[label] = per_label_total.get(label, 0) + 1
            if pred_any:
                per_label_hit[label] = per_label_hit.get(label, 0) + 1
        if gold_pos and pred_any:
            tp += 1
        elif (not gold_pos) and pred_any:
            fp += 1
        elif gold_pos and (not pred_any):
            fn += 1
    per_label = {}
    for l in sorted(positive_labels):
        total = per_label_total.get(l, 0)
        hit = per_label_hit.get(l, 0)
        per_label[str(l)] = {"total": total, "hit": hit, "recall": (hit / total) if total else 0.0}
    return {"overall": compute_prf(tp, fp, fn), "per_label": per_label}


def collect_identifier_mismatches(
    gold_rows: list[dict],
    pred_rows: dict[str, dict[str, set[str]]],
) -> tuple[list[dict], list[dict]]:
    any_rows: list[dict] = []
    category_rows: list[dict] = []
    for row in gold_rows:
        project = row["project"]
        parameter = row["parameter"]
        label = int(row["label"])

        preds = pred_rows.get(project, {"regular": set(), "container": set()})
        pred_regular = parameter in preds.get("regular", set())
        pred_container = parameter in preds.get("container", set())
        pred_any = pred_regular or pred_container

        gold_regular = label in {1, 3}
        gold_container = label == 2
        gold_any = gold_regular or gold_container

        if pred_any != gold_any:
            any_rows.append(
                {
                    "project": project,
                    "parameter": parameter,
                    "gold_label": label,
                    "pred_regular": int(pred_regular),
                    "pred_container": int(pred_container),
                    "error_type": "fp" if (pred_any and not gold_any) else "fn",
                }
            )

        if gold_any and pred_any:
            if gold_container and (not pred_container):
                category_rows.append(
                    {
                        "project": project,
                        "parameter": parameter,
                        "gold_label": label,
                        "pred_regular": int(pred_regular),
                        "pred_container": int(pred_container),
                        "error_type": "gold_container_pred_regular",
                    }
                )
            if gold_regular and (not pred_regular):
                category_rows.append(
                    {
                        "project": project,
                        "parameter": parameter,
                        "gold_label": label,
                        "pred_regular": int(pred_regular),
                        "pred_container": int(pred_container),
                        "error_type": "gold_regular_pred_container",
                    }
                )
    return any_rows, category_rows


def compute_identifier_missed_label3_ratio(
    gold_rows: list[dict],
    pred_rows: dict[str, dict[str, set[str]]],
) -> dict:
    fn_total = 0
    fn_label3 = 0
    for row in gold_rows:
        project = row["project"]
        parameter = row["parameter"]
        label = int(row["label"])

        preds = pred_rows.get(project, {"regular": set(), "container": set()})
        pred_any = (parameter in preds.get("regular", set())) or (parameter in preds.get("container", set()))

        gold_any = label in {1, 2, 3}
        if gold_any and (not pred_any):
            fn_total += 1
            if label == 3:
                fn_label3 += 1

    ratio = (fn_label3 / fn_total) if fn_total else 0.0
    return {"fn_total": fn_total, "fn_label3": fn_label3, "ratio": ratio}


def is_chain_step_dict(obj) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    for k in obj.keys():
        if not isinstance(k, str) or not k.isdigit():
            return False
    return True


def iter_chain_dicts(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from iter_chain_dicts(x)
        return
    if isinstance(obj, dict):
        if is_chain_step_dict(obj):
            yield obj
            return
        for v in obj.values():
            yield from iter_chain_dicts(v)


def iter_chain_endpoints(obj):
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            yield s
        return
    if isinstance(obj, list):
        for x in obj:
            yield from iter_chain_endpoints(x)
        return
    if isinstance(obj, dict):
        items = list(obj.items())
        if all(isinstance(k, str) and k.isdigit() for k, _ in items):
            items.sort(key=lambda kv: int(kv[0]))
        for _, v in items:
            yield from iter_chain_endpoints(v)


def load_project_chains(dep_fp: Path) -> list[list[str]]:
    try:
        obj = json.loads(dep_fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    chains = []
    for chain in iter_chain_dicts(obj):
        seq = [e for e in iter_chain_endpoints(chain)]
        if seq:
            chains.append(seq)
    return chains


def relation_exists(chains: list[list[str]], upstream: str, downstream: str) -> bool:
    for seq in chains:
        ups = [i for i, x in enumerate(seq) if x == upstream]
        if not ups:
            continue
        downs = [i for i, x in enumerate(seq) if x == downstream]
        if not downs:
            continue
        for i in ups:
            for j in downs:
                if i < j:
                    return True
    return False


def endpoints_cooccur(chains: list[list[str]], upstream: str, downstream: str) -> bool:
    for seq in chains:
        if upstream in seq and downstream in seq:
            return True
    return False


def eval_cads_pairs(gold_rows: list[dict], cache_root: Path) -> tuple[dict, list[dict]]:
    total = len(gold_rows)
    found = 0
    correct = 0
    debug_rows: list[dict] = []
    chains_by_project: dict[str, list[list[str]]] = {}
    for row in gold_rows:
        project = row["project"]
        if project in chains_by_project:
            continue
        fp = cache_root / project / "dependency_chains_results.json"
        if fp.exists():
            chains_by_project[project] = load_project_chains(fp)
        else:
            chains_by_project[project] = []

    for row in gold_rows:
        project = row["project"]
        upstream = row["upstream_api"]
        downstream = row["downstream_api"]
        chains = chains_by_project.get(project, [])
        if not chains:
            debug_rows.append(
                {
                    "project": project,
                    "upstream_api": upstream,
                    "downstream_api": downstream,
                    "error_type": "missing_project_file",
                }
            )
            continue

        cooccur = endpoints_cooccur(chains, upstream, downstream)
        if cooccur:
            found += 1
        else:
            debug_rows.append(
                {
                    "project": project,
                    "upstream_api": upstream,
                    "downstream_api": downstream,
                    "error_type": "endpoints_not_in_same_chain",
                }
            )
            continue

        ok = relation_exists(chains, upstream, downstream)
        if ok:
            correct += 1
        else:
            debug_rows.append(
                {
                    "project": project,
                    "upstream_api": upstream,
                    "downstream_api": downstream,
                    "error_type": "order_mismatch",
                }
            )

    fp = max(0, found - correct)
    precision = (correct / found) if found else 0.0
    recall = (correct / total) if total else 0.0
    return (
        {
            "total": total,
            "found": found,
            "correct": correct,
            "tp": correct,
            "fp": fp,
            "precision": precision,
            "recall": recall,
        },
        debug_rows,
    )


def load_pred_groups_with_duplicates(
    cache_root: Path, projects: set[str]
) -> tuple[dict[tuple[str, str], str], list[dict]]:
    pred: dict[tuple[str, str], str] = {}
    dups: dict[tuple[str, str], set[str]] = {}
    if not cache_root.exists():
        return pred, []
    for project_dir in cache_root.iterdir():
        if not project_dir.is_dir():
            continue
        project = project_dir.name
        if project not in projects:
            continue
        fp = project_dir / "api_doc_with_type.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for group_obj in data:
            if not isinstance(group_obj, dict):
                continue
            for group_name, group_value in group_obj.items():
                if not isinstance(group_name, str) or not isinstance(group_value, dict):
                    continue
                g = group_name.strip()
                if not g:
                    continue
                for api_name, api_detail in group_value.items():
                    if api_name == "__inherited_params__":
                        continue
                    if not isinstance(api_name, str) or not isinstance(api_detail, dict):
                        continue
                    api_key = api_name.strip()
                    k = (project, api_key)
                    existing = pred.get(k)
                    if existing is None:
                        pred[k] = g
                    elif existing != g:
                        dups.setdefault(k, {existing}).add(g)
    dup_rows = [
        {
            "project": k[0],
            "api_name": k[1],
            "pred_groups": json.dumps(sorted(v), ensure_ascii=False),
        }
        for k, v in sorted(dups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
    return pred, dup_rows


def load_pred_groups(cache_root: Path, projects: set[str]) -> dict[tuple[str, str], str]:
    pred, _ = load_pred_groups_with_duplicates(cache_root, projects)
    return pred


def collect_group_pair_mismatches(
    gold_groups: dict[str, dict[str, str]],
    group_pred: dict[tuple[str, str], str],
) -> list[dict]:
    rows: list[dict] = []
    for project, api_to_gold in gold_groups.items():
        apis = sorted(api_to_gold.keys())
        for a, b in combinations(apis, 2):
            gold_a = api_to_gold.get(a, "")
            gold_b = api_to_gold.get(b, "")
            pred_a = group_pred.get((project, a), "")
            pred_b = group_pred.get((project, b), "")
            gold_same = gold_a == gold_b
            pred_same = (pred_a != "" and pred_b != "" and pred_a == pred_b)
            if gold_same == pred_same:
                continue
            rows.append(
                {
                    "project": project,
                    "api_a": a,
                    "api_b": b,
                    "gold_group_a": gold_a,
                    "gold_group_b": gold_b,
                    "pred_group_a": pred_a,
                    "pred_group_b": pred_b,
                    "gold_same": int(gold_same),
                    "pred_same": int(pred_same),
                    "error_type": "fp" if pred_same and (not gold_same) else "fn",
                }
            )
    return rows


def collect_group_api_mismatches(
    gold_groups: dict[str, dict[str, str]],
    group_pred: dict[tuple[str, str], str],
) -> list[dict]:
    rows: list[dict] = []
    for project, api_to_gold in gold_groups.items():
        for api_name, gold_group in api_to_gold.items():
            pred_group = group_pred.get((project, api_name), "")
            if not pred_group:
                rows.append(
                    {
                        "project": project,
                        "api_name": api_name,
                        "gold_group": gold_group,
                        "pred_group": "",
                        "error_type": "missing",
                    }
                )
                continue
            if pred_group != gold_group:
                rows.append(
                    {
                        "project": project,
                        "api_name": api_name,
                        "gold_group": gold_group,
                        "pred_group": pred_group,
                        "error_type": "mismatch",
                    }
                )
    return rows


def main() -> int:
    args = parse_args()
    root = Path(args.root)

    gold_groups = load_gold_groups(root / args.gold_groups)
    gold_type = load_gold_type(root / args.gold_type)
    gold_mapping = load_gold_mapping(root / args.gold_mapping)
    gold_identifier = load_gold_identifier_dataset(Path(args.gold_identifiers))
    gold_cads = load_gold_cads_pairs(root / args.gold_cads)

    projects_for_groups = set(gold_groups.keys())
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    result = {}
    summary_rows_csv: list[dict] = []
    summary_rows_md: list[list[str]] = []
    overview_rows: list[list[str]] = []
    overview_numeric_rows: list[dict] = []
    identifier12_summary_rows: list[dict] = []
    identifier_pred_any_by_model: dict[str, dict[str, set[str]]] = {}
    for model in models:
        cache_root = resolve_cache_dir(root, model)
        type_pred = load_type_pred(cache_root)
        mapping_pred = load_mapping_pred(cache_root)
        identifier_pred = load_identifier_pred(cache_root)
        identifier_pred_any_by_model[model] = {
            project: set(d.get("regular", set())) | set(d.get("container", set()))
            for project, d in identifier_pred.items()
        }
        group_pred, group_dup_rows = load_pred_groups_with_duplicates(
            cache_root, projects_for_groups
        )

        group_per_project = {}
        group_tp = group_fp = group_fn = group_pairs = group_apis = 0
        for project, api_to_gold in gold_groups.items():
            api_to_pred = {api: group_pred.get((project, api)) for api in api_to_gold.keys()}
            m = compute_pairwise(api_to_gold, api_to_pred)
            group_per_project[project] = m
            group_tp += m["tp"]
            group_fp += m["fp"]
            group_fn += m["fn"]
            group_pairs += m["pairs"]
            group_apis += m["apis"]

        group_precision = (group_tp / (group_tp + group_fp)) if (group_tp + group_fp) else 0.0
        group_recall = (group_tp / (group_tp + group_fn)) if (group_tp + group_fn) else 0.0
        group_f1 = (
            (2 * group_precision * group_recall / (group_precision + group_recall))
            if (group_precision + group_recall)
            else 0.0
        )

        cads_metrics = {
            "total": 0,
            "found": 0,
            "correct": 0,
            "tp": 0,
            "fp": 0,
            "precision": 0.0,
            "recall": 0.0,
        }
        cads_debug_rows: list[dict] = []
        if gold_cads:
            cads_metrics, cads_debug_rows = eval_cads_pairs(gold_cads, cache_root)

        result[model] = {
            "functional_group_identification": {
                "overall": {
                    "projects": len(group_per_project),
                    "apis": group_apis,
                    "pairs": group_pairs,
                    "tp": group_tp,
                    "fp": group_fp,
                    "fn": group_fn,
                    "precision": group_precision,
                    "recall": group_recall,
                    "f1": group_f1,
                },
                "per_project": group_per_project,
            },
            "parameter_mapping_topk": eval_mapping_topk(
                gold_mapping, mapping_pred, topk=max(1, int(args.mapping_topk))
            ),
            "api_operation_semantics": eval_type(gold_type, type_pred),
            "object_identifier_parameter_recognition": eval_identifiers(
                gold_identifier, identifier_pred
            ),
            "object_identifier_parameter_recognition_label12": eval_identifiers_subset(
                gold_identifier,
                identifier_pred,
                positive_labels={1, 2},
                ignore_labels={3},
            ),
            "cads_dependency_pairs": cads_metrics,
        }

        fg_overall = result[model]["functional_group_identification"]["overall"]
        pm = result[model]["parameter_mapping_topk"]
        op = result[model]["api_operation_semantics"]
        oid = result[model]["object_identifier_parameter_recognition"]["overall"]
        oid12 = result[model]["object_identifier_parameter_recognition_label12"]
        oid12_overall = oid12.get("overall", {})
        oid12_per_label = oid12.get("per_label", {}) if isinstance(oid12, dict) else {}

        fg_tp = to_int(fg_overall.get("tp"))
        fg_fp = to_int(fg_overall.get("fp"))
        fg_prec = to_float(fg_overall.get("precision"))
        fg_rec = to_float(fg_overall.get("recall"))

        pm_tp = to_int(pm.get("correct"))
        pm_fp = max(0, to_int(pm.get("found")) - pm_tp)
        pm_prec = to_float(pm.get("precision"))
        pm_rec = to_float(pm.get("recall"))

        op_tp = to_int(op.get("correct"))
        op_fp = max(0, to_int(op.get("found")) - op_tp)
        op_prec = to_float(op.get("precision"))
        op_rec = to_float(op.get("recall"))
        op_f1 = to_float(op.get("f1"))

        oid_tp = to_int(oid.get("tp"))
        oid_fp = to_int(oid.get("fp"))
        oid_prec = to_float(oid.get("precision"))
        oid_rec = to_float(oid.get("recall"))
        oid_missed3 = compute_identifier_missed_label3_ratio(gold_identifier, identifier_pred)
        oid_fn_total = to_int(oid_missed3.get("fn_total"))
        oid_fn_label3 = to_int(oid_missed3.get("fn_label3"))
        oid_fn_label3_ratio = to_float(oid_missed3.get("ratio"))

        per_model_prec_items: list[float] = []
        per_model_rec_items: list[float] = []
        if to_int(fg_overall.get("pairs")) > 0:
            per_model_prec_items.append(fg_prec)
            per_model_rec_items.append(fg_rec)
        if to_int(pm.get("total")) > 0:
            per_model_prec_items.append(pm_prec)
            per_model_rec_items.append(pm_rec)
        if to_int(op.get("total")) > 0:
            per_model_prec_items.append(op_prec)
            per_model_rec_items.append(op_rec)
        if (oid_tp + oid_fp + to_int(oid.get("fn"))) > 0:
            per_model_prec_items.append(oid_prec)
            per_model_rec_items.append(oid_rec)
        if gold_cads and to_int(cads_metrics.get("total")) > 0:
            per_model_prec_items.append(to_float(cads_metrics.get("precision")))
            per_model_rec_items.append(to_float(cads_metrics.get("recall")))

        per_model_avg_prec = avg(per_model_prec_items)
        per_model_avg_rec = avg(per_model_rec_items)

        fg_f1 = to_float(fg_overall.get("f1"))
        pm_f1 = safe_f1(pm_prec, pm_rec)
        oid_f1 = safe_f1(oid_prec, oid_rec)

        f1_items: list[float] = []
        if to_int(fg_overall.get("pairs")) > 0:
            f1_items.append(fg_f1)
        if to_int(pm.get("total")) > 0:
            f1_items.append(pm_f1)
        if to_int(op.get("total")) > 0:
            f1_items.append(op_f1)
        if (oid_tp + oid_fp + to_int(oid.get("fn"))) > 0:
            f1_items.append(oid_f1)
        overall_f1 = (sum(f1_items) / len(f1_items)) if f1_items else 0.0

        summary_rows_csv.append(
            {
                "model": model,
                "fg_tp": fg_tp,
                "fg_fp": fg_fp,
                "fg_prec": format_pct(fg_prec),
                "fg_rec": format_pct(fg_rec),
                "fg_f1": format_pct(fg_f1),
                "pm_tp": pm_tp,
                "pm_fp": pm_fp,
                "pm_prec": format_pct(pm_prec),
                "pm_rec": format_pct(pm_rec),
                "pm_f1": format_pct(pm_f1),
                "op_tp": op_tp,
                "op_fp": op_fp,
                "op_prec": format_pct(op_prec),
                "op_rec": format_pct(op_rec),
                "op_f1": format_pct(op_f1),
                "id_tp": oid_tp,
                "id_fp": oid_fp,
                "id_prec": format_pct(oid_prec),
                "id_rec": format_pct(oid_rec),
                "id_f1": format_pct(oid_f1),
                "id_fn_total": oid_fn_total,
                "id_fn_label3": oid_fn_label3,
                "id_fn_label3_ratio": format_pct(oid_fn_label3_ratio),
                "cads_tp": to_int(cads_metrics.get("tp")),
                "cads_fp": to_int(cads_metrics.get("fp")),
                "cads_prec": format_pct(to_float(cads_metrics.get("precision"))),
                "cads_rec": format_pct(to_float(cads_metrics.get("recall"))),
                "overall_f1": format_pct(overall_f1),
            }
        )

        label1 = oid12_per_label.get("1", {}) if isinstance(oid12_per_label, dict) else {}
        label2 = oid12_per_label.get("2", {}) if isinstance(oid12_per_label, dict) else {}
        identifier12_summary_rows.append(
            {
                "model": model,
                "tp": to_int(oid12_overall.get("tp")),
                "fp": to_int(oid12_overall.get("fp")),
                "fn": to_int(oid12_overall.get("fn")),
                "precision": format_pct(to_float(oid12_overall.get("precision"))),
                "recall": format_pct(to_float(oid12_overall.get("recall"))),
                "f1": format_pct(to_float(oid12_overall.get("f1"))),
                "label1_total": to_int(label1.get("total")),
                "label1_hit": to_int(label1.get("hit")),
                "label1_recall": format_pct(to_float(label1.get("recall"))),
                "label2_total": to_int(label2.get("total")),
                "label2_hit": to_int(label2.get("hit")),
                "label2_recall": format_pct(to_float(label2.get("recall"))),
            }
        )

        summary_rows_md.append(
            [
                model,
                str(fg_tp),
                str(fg_fp),
                format_pct(fg_prec),
                format_pct(fg_rec),
                format_pct(fg_f1),
                str(pm_tp),
                str(pm_fp),
                format_pct(pm_prec),
                format_pct(pm_rec),
                format_pct(pm_f1),
                str(op_tp),
                str(op_fp),
                format_pct(op_prec),
                format_pct(op_rec),
                format_pct(op_f1),
                str(oid_tp),
                str(oid_fp),
                format_pct(oid_prec),
                format_pct(oid_rec),
                format_pct(oid_f1),
                str(to_int(cads_metrics.get("tp"))),
                str(to_int(cads_metrics.get("fp"))),
                format_pct(to_float(cads_metrics.get("precision"))),
                format_pct(to_float(cads_metrics.get("recall"))),
                format_pct(overall_f1),
            ]
        )

        overview_rows.append(
            [
                model,
                str(fg_tp),
                str(fg_fp),
                format_pct(fg_prec),
                format_pct(fg_rec),
                str(pm_tp),
                str(pm_fp),
                format_pct(pm_prec),
                format_pct(pm_rec),
                str(op_tp),
                str(op_fp),
                format_pct(op_prec),
                str(oid_tp),
                str(oid_fp),
                format_pct(oid_prec),
                format_pct(oid_rec),
                format_pct(oid_fn_label3_ratio),
                str(to_int(cads_metrics.get("tp"))),
                str(to_int(cads_metrics.get("fp"))),
                format_pct(to_float(cads_metrics.get("precision"))),
                format_pct(to_float(cads_metrics.get("recall"))),
                format_pct(per_model_avg_prec),
                format_pct(per_model_avg_rec),
            ]
        )
        overview_numeric_rows.append(
            {
                "fg_tp": fg_tp,
                "fg_fp": fg_fp,
                "fg_prec": fg_prec,
                "fg_rec": fg_rec,
                "pm_tp": pm_tp,
                "pm_fp": pm_fp,
                "pm_prec": pm_prec,
                "pm_rec": pm_rec,
                "op_tp": op_tp,
                "op_fp": op_fp,
                "op_prec": op_prec,
                "op_rec": op_rec,
                "id_tp": oid_tp,
                "id_fp": oid_fp,
                "id_prec": oid_prec,
                "id_rec": oid_rec,
                "id_fn_label3_ratio": oid_fn_label3_ratio,
                "cads_tp": to_int(cads_metrics.get("tp")),
                "cads_fp": to_int(cads_metrics.get("fp")),
                "cads_prec": to_float(cads_metrics.get("precision")),
                "cads_rec": to_float(cads_metrics.get("recall")),
                "avg_prec": per_model_avg_prec,
                "avg_rec": per_model_avg_rec,
            }
        )

        if args.debug:
            debug_dir = root / args.debug_out_dir
            debug_dir.mkdir(parents=True, exist_ok=True)

            type_mismatches = collect_type_mismatches(gold_type, type_pred)
            if type_mismatches:
                write_csv(
                    debug_dir / f"{model}_api_type_mismatches.csv",
                    ["project", "api_name", "gold_type", "pred_type", "error_type"],
                    type_mismatches,
                )

            mapping_mismatches = collect_mapping_mismatches(
                gold_mapping, mapping_pred, topk=max(1, int(args.mapping_topk))
            )
            if mapping_mismatches:
                write_csv(
                    debug_dir / f"{model}_param_mapping_mismatches.csv",
                    [
                        "project",
                        "api_name",
                        "parameter_name",
                        "gold_keep_pra",
                        "gold_replace_para",
                        "pred_topk",
                        "error_type",
                    ],
                    mapping_mismatches,
                )

            any_mismatches, category_mismatches = collect_identifier_mismatches(
                gold_identifier, identifier_pred
            )
            if any_mismatches:
                write_csv(
                    debug_dir / f"{model}_identifier_any_mismatches.csv",
                    ["project", "parameter", "gold_label", "pred_regular", "pred_container", "error_type"],
                    any_mismatches,
                )
            if category_mismatches:
                write_csv(
                    debug_dir / f"{model}_identifier_category_mismatches.csv",
                    ["project", "parameter", "gold_label", "pred_regular", "pred_container", "error_type"],
                    category_mismatches,
                )

            if gold_groups:
                group_api_mismatches = collect_group_api_mismatches(gold_groups, group_pred)
                if group_api_mismatches:
                    write_csv(
                        debug_dir / f"{model}_functional_group_api_mismatches.csv",
                        ["project", "api_name", "gold_group", "pred_group", "error_type"],
                        group_api_mismatches,
                    )

                if group_dup_rows:
                    write_csv(
                        debug_dir / f"{model}_functional_group_duplicate_apis.csv",
                        ["project", "api_name", "pred_groups"],
                        group_dup_rows,
                    )

                group_pair_mismatches = collect_group_pair_mismatches(gold_groups, group_pred)
                if group_pair_mismatches:
                    write_csv(
                        debug_dir / f"{model}_functional_group_pair_mismatches.csv",
                        [
                            "project",
                            "api_a",
                            "api_b",
                            "gold_group_a",
                            "gold_group_b",
                            "pred_group_a",
                            "pred_group_b",
                            "gold_same",
                            "pred_same",
                            "error_type",
                        ],
                        group_pair_mismatches,
                    )

            if gold_cads and cads_debug_rows:
                write_csv(
                    debug_dir / f"{model}_cads_dependency_pair_mismatches.csv",
                    ["project", "upstream_api", "downstream_api", "error_type"],
                    cads_debug_rows,
                )

    out_fp = root / args.out_json
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    out_fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] all experiment metrics -> {out_fp}")

    summary_csv_fp = root / args.out_summary_csv
    write_csv(
        summary_csv_fp,
        [
            "model",
            "fg_tp",
            "fg_fp",
            "fg_prec",
            "fg_rec",
            "fg_f1",
            "pm_tp",
            "pm_fp",
            "pm_prec",
            "pm_rec",
            "pm_f1",
            "op_tp",
            "op_fp",
            "op_prec",
            "op_rec",
            "op_f1",
            "id_tp",
            "id_fp",
            "id_prec",
            "id_rec",
            "id_f1",
            "id_fn_total",
            "id_fn_label3",
            "id_fn_label3_ratio",
            "cads_tp",
            "cads_fp",
            "cads_prec",
            "cads_rec",
            "overall_f1",
        ],
        summary_rows_csv,
    )
    print(f"[ok] summary table (csv) -> {summary_csv_fp}")

    summary_md_fp = root / args.out_summary_md
    write_markdown_table(
        summary_md_fp,
        [
            "model",
            "FG_TP",
            "FG_FP",
            "FG_Prec",
            "FG_Rec",
            "FG_F1",
            "PM_TP",
            "PM_FP",
            "PM_Prec",
            "PM_Rec",
            "PM_F1",
            "OP_TP",
            "OP_FP",
            "OP_Prec",
            "OP_Rec",
            "OP_F1",
            "ID_TP",
            "ID_FP",
            "ID_Prec",
            "ID_Rec",
            "ID_F1",
            "CADS_TP",
            "CADS_FP",
            "CADS_Prec",
            "CADS_Rec",
            "Overall_F1",
        ],
        summary_rows_md,
    )
    print(f"[ok] summary table (md) -> {summary_md_fp}")

    overview_headers = [
        "Model",
        "TP",
        "FP",
        "Prec",
        "Rec",
        "TP",
        "FP",
        "Prec",
        "Rec",
        "TP",
        "FP",
        "Prec",
        "TP",
        "FP",
        "Prec",
        "Rec",
        "Missed FN(Label=3)%",
        "CADS_TP",
        "CADS_FP",
        "CADS_Prec",
        "CADS_Rec",
        "Avg Prec",
        "Avg Rec",
    ]

    if overview_numeric_rows:
        n = len(overview_numeric_rows)
        fg_tp_avg = sum(r["fg_tp"] for r in overview_numeric_rows) / n
        fg_fp_avg = sum(r["fg_fp"] for r in overview_numeric_rows) / n
        fg_prec_avg = sum(r["fg_prec"] for r in overview_numeric_rows) / n
        fg_rec_avg = sum(r["fg_rec"] for r in overview_numeric_rows) / n

        pm_tp_avg = sum(r["pm_tp"] for r in overview_numeric_rows) / n
        pm_fp_avg = sum(r["pm_fp"] for r in overview_numeric_rows) / n
        pm_prec_avg = sum(r["pm_prec"] for r in overview_numeric_rows) / n
        pm_rec_avg = sum(r["pm_rec"] for r in overview_numeric_rows) / n

        op_tp_avg = sum(r["op_tp"] for r in overview_numeric_rows) / n
        op_fp_avg = sum(r["op_fp"] for r in overview_numeric_rows) / n
        op_prec_avg = sum(r["op_prec"] for r in overview_numeric_rows) / n
        op_rec_avg = sum(r["op_rec"] for r in overview_numeric_rows) / n

        id_tp_avg = sum(r["id_tp"] for r in overview_numeric_rows) / n
        id_fp_avg = sum(r["id_fp"] for r in overview_numeric_rows) / n
        id_prec_avg = sum(r["id_prec"] for r in overview_numeric_rows) / n
        id_rec_avg = sum(r["id_rec"] for r in overview_numeric_rows) / n
        id_fn_label3_ratio_avg = sum(r.get("id_fn_label3_ratio", 0.0) for r in overview_numeric_rows) / n

        cads_tp_avg = sum(r.get("cads_tp", 0) for r in overview_numeric_rows) / n
        cads_fp_avg = sum(r.get("cads_fp", 0) for r in overview_numeric_rows) / n
        cads_prec_avg = sum(r.get("cads_prec", 0.0) for r in overview_numeric_rows) / n
        cads_rec_avg = sum(r.get("cads_rec", 0.0) for r in overview_numeric_rows) / n
        prec_items = [fg_prec_avg, pm_prec_avg, op_prec_avg, id_prec_avg]
        rec_items = [fg_rec_avg, pm_rec_avg, op_rec_avg, id_rec_avg]
        if gold_cads:
            prec_items.append(cads_prec_avg)
            rec_items.append(cads_rec_avg)
        overall_prec_avg = avg(prec_items)
        overall_rec_avg = avg(rec_items)

        overview_rows.append(
            [
                "AVG(models)",
                format_num(fg_tp_avg),
                format_num(fg_fp_avg),
                format_pct(fg_prec_avg),
                format_pct(fg_rec_avg),
                format_num(pm_tp_avg),
                format_num(pm_fp_avg),
                format_pct(pm_prec_avg),
                format_pct(pm_rec_avg),
                format_num(op_tp_avg),
                format_num(op_fp_avg),
                format_pct(op_prec_avg),
                format_num(id_tp_avg),
                format_num(id_fp_avg),
                format_pct(id_prec_avg),
                format_pct(id_rec_avg),
                format_pct(id_fn_label3_ratio_avg),
                format_num(cads_tp_avg),
                format_num(cads_fp_avg),
                format_pct(cads_prec_avg),
                format_pct(cads_rec_avg),
                format_pct(overall_prec_avg),
                format_pct(overall_rec_avg),
            ]
        )

    overview_csv_fp = root / args.out_overview_csv
    write_overview_csv(overview_csv_fp, overview_rows)
    print(f"[ok] overview table (csv) -> {overview_csv_fp}")
    if not args.no_version_drift_plot:
        versions = [m.strip() for m in args.models.split(",") if m.strip()]
        labels: list[str] = []
        for idx, m in enumerate(versions):
            if "_v" in m and m.rsplit("_v", 1)[-1].isdigit():
                labels.append(f"v{m.rsplit('_v', 1)[-1]}")
            else:
                labels.append("v1" if idx == 0 else m)

        base_name = (versions[0] if versions else "model").replace("/", "_")
        out_version_table = root / f"scripts/version_drift_{base_name}_table.csv"
        out_version_pdf = root / f"scripts/version_drift_{base_name}_with_benchmark.pdf"
        scripts_dir = Path(__file__).resolve().parent
        export_script = scripts_dir / "export_version_drift_table.py"
        plot_script = scripts_dir / "plot_version_drift_pdf.py"

        cmd_export = [
            sys.executable,
            str(export_script),
            "--in-overview-csv",
            str(overview_csv_fp),
            "--out-csv",
            str(out_version_table),
            "--versions",
            ",".join(versions),
            "--labels",
            ",".join(labels),
        ]
        r1 = subprocess.run(cmd_export, capture_output=True, text=True)
        if r1.returncode != 0:
            err = (r1.stderr or r1.stdout or "").strip()
            print(f"[warn] version drift table generation failed: {err}")
        else:
            print(f"[ok] version drift table (csv) -> {out_version_table}")
            bench_prec = (
                args.benchmark_prec
                if (args.benchmark_prec or "").strip()
                else (args.benchmark_acc or "")
            )
            cmd_plot = [
                sys.executable,
                str(plot_script),
                "--in-csv",
                str(out_version_table),
                "--out-pdf",
                str(out_version_pdf),
                "--versions",
                ",".join(versions),
                "--labels",
                ",".join(labels),
                "--benchmark-prec",
                str(bench_prec),
                "--benchmark-rec",
                str(args.benchmark_rec),
                "--testset-csv",
                str(root / args.testset_csv) if not Path(args.testset_csv).is_absolute() else str(args.testset_csv),
                "--testset-fp-p",
                str(args.testset_fp_p),
                "--testset-mode",
                str(args.testset_mode),
            ]
            r2 = subprocess.run(cmd_plot, capture_output=True, text=True)
            if r2.returncode != 0:
                err = (r2.stderr or r2.stdout or "").strip()
                print(f"[warn] version drift pdf generation failed: {err}")
                if "No module named 'matplotlib'" in err or 'No module named "matplotlib"' in err:
                    print("[hint] install matplotlib in the current python environment: python3 -m pip install matplotlib")
            else:
                print(f"[ok] version drift pdf -> {out_version_pdf}")

    if identifier12_summary_rows:
        identifier12_summary_fp = root / args.out_identifier12_summary_csv
        write_csv(
            identifier12_summary_fp,
            [
                "model",
                "tp",
                "fp",
                "fn",
                "precision",
                "recall",
                "f1",
                "label1_total",
                "label1_hit",
                "label1_recall",
                "label2_total",
                "label2_hit",
                "label2_recall",
            ],
            identifier12_summary_rows,
        )
        print(f"[ok] identifier( label in {{1,2}}, ignore {{3}} ) summary -> {identifier12_summary_fp}")

    if gold_identifier and identifier_pred_any_by_model:
        report_rows: list[dict] = []
        missed_all_rows: list[dict] = []
        model_count = len(models)
        all_mask = (1 << model_count) - 1 if model_count > 0 else 0

        for row in gold_identifier:
            project = row["project"]
            parameter = row["parameter"]
            label = int(row["label"])
            mask = 0
            for idx, model in enumerate(models):
                pred_map = identifier_pred_any_by_model.get(model, {})
                if parameter in pred_map.get(project, set()):
                    mask |= 1 << idx
            hit_models = mask_to_models(mask, models)
            miss_models = [m for m in models if m not in set(hit_models)]
            hit_pattern = pattern_key(mask=mask, model_count=model_count)
            missed = label in {1, 2, 3} and mask == 0

            record = {
                "project": project,
                "parameter": parameter,
                "label": str(label),
                "hit_models": "|".join(hit_models),
                "hit_pattern": hit_pattern,
                "miss_models": "|".join(miss_models),
                "missed_all_models": "1" if missed else "0",
            }
            report_rows.append(record)
            if missed:
                missed_all_rows.append(record)

        report_fp = root / args.out_identifier_gt_report_csv
        write_csv(
            report_fp,
            [
                "project",
                "parameter",
                "label",
                "hit_models",
                "hit_pattern",
                "miss_models",
                "missed_all_models",
            ],
            report_rows,
        )
        print(f"[ok] identifier gt hit report -> {report_fp}")

        missed_all_fp = root / args.out_identifier_gt_missed_all_csv
        write_csv(
            missed_all_fp,
            [
                "project",
                "parameter",
                "label",
                "hit_models",
                "hit_pattern",
                "miss_models",
                "missed_all_models",
            ],
            missed_all_rows,
        )
        print(f"[ok] identifier gt missed(all models) -> {missed_all_fp} (rows={len(missed_all_rows)})")

    overview_md_fp = root / args.out_overview_md
    html_table_lines: list[str] = []
    html_table_lines.append("<table>")
    html_table_lines.append("  <thead>")
    html_table_lines.append("    <tr>")
    html_table_lines.append("      <th rowspan=\"2\">模型</th>")
    html_table_lines.append("      <th colspan=\"4\">Functional-group identification</th>")
    html_table_lines.append("      <th colspan=\"4\">Parameter mapping</th>")
    html_table_lines.append("      <th colspan=\"3\">API operation semantics</th>")
    html_table_lines.append("      <th colspan=\"5\">Object identifier recognition</th>")
    html_table_lines.append("      <th colspan=\"4\">CADS dependency</th>")
    html_table_lines.append("      <th colspan=\"2\">Overall</th>")
    html_table_lines.append("    </tr>")
    html_table_lines.append("    <tr>")
    for h in overview_headers[1:]:
        html_table_lines.append(f"      <th>{h}</th>")
    html_table_lines.append("    </tr>")
    html_table_lines.append("  </thead>")
    html_table_lines.append("  <tbody>")
    for row in overview_rows:
        html_table_lines.append("    <tr>")
        for cell in row:
            html_table_lines.append(f"      <td>{cell}</td>")
        html_table_lines.append("    </tr>")
    html_table_lines.append("  </tbody>")
    html_table_lines.append("</table>")
    overview_html = "\n".join(html_table_lines) + "\n"
    overview_html_fp = root / args.out_overview_html
    write_html(overview_html_fp, overview_html)
    print(f"[ok] overview table (html) -> {overview_html_fp}")
    if args.out_overview_md:
        overview_md_fp.parent.mkdir(parents=True, exist_ok=True)
        overview_md_fp.write_text(overview_html, encoding="utf-8")
        print(f"[ok] overview table (md) -> {overview_md_fp}")
    if args.print_overview_table:
        print(overview_html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
