#!/usr/bin/env python3
import argparse
import ast
import csv
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估接口类型与参数映射能力")
    parser.add_argument(
        "--models",
        default="gpt-4o-mini,gpt-5.1",
        help="要评估的模型列表，逗号分隔（默认: gpt-4o-mini,gpt-5.1）",
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="项目根目录",
    )
    parser.add_argument(
        "--gold-type",
        default="scripts/manual_label_api_type_ground_truth.csv",
        help="类型任务人工标注 CSV（相对 root）",
    )
    parser.add_argument(
        "--gold-mapping",
        default="scripts/manual_label_param_mapping_ground_truth.csv",
        help="参数映射任务人工标注 CSV（相对 root）",
    )
    parser.add_argument(
        "--out-json",
        default="scripts/eval_metrics_result.json",
        help="评估结果 JSON 输出路径（相对 root）",
    )
    parser.add_argument(
        "--gold-identifier",
        default="scripts/identifier_value.csv",
        help="identifier 参数人工标注 CSV（相对 root）",
    )
    parser.add_argument(
        "--debug-keep-pra-mismatch",
        action="store_true",
        help="导出 keep_pra 不一致的调试明细",
    )
    parser.add_argument(
        "--debug-out-csv",
        default="scripts/eval_keep_pra_mismatch_records.csv",
        help="keep_pra 不一致调试明细输出路径（相对 root）",
    )
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
                    "is_false_positive": str(
                        (r.get("是否为误报") or r.get("is_false_positive") or "")
                    ).strip()
                    == "1",
                }
            )
    return rows


def load_gold_identifier(csv_fp: Path) -> dict[str, dict[str, int]]:
    gold: dict[str, dict[str, int]] = {}
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = {str(x or "").strip() for x in (reader.fieldnames or [])}
        label_keys = [k for k in ("label", "is_identifier") if k in fieldnames]
        for r in reader:
            project = (r.get("project") or "").strip()
            parameter = (r.get("parameter") or "").strip()
            if not project or not parameter:
                continue
            label = 1
            for k in label_keys:
                raw = (r.get(k) or "").strip()
                if raw == "":
                    continue
                if raw.lower() in {"true", "yes"}:
                    label = 1
                    break
                if raw.lower() in {"false", "no"}:
                    label = 0
                    break
                try:
                    label = 1 if int(raw) == 1 else 0
                    break
                except Exception:
                    label = 1
            bucket = gold.setdefault(project, {})
            prev = bucket.get(parameter)
            if prev is None:
                bucket[parameter] = label
            else:
                bucket[parameter] = 1 if (prev == 1 or label == 1) else 0
    return gold


def load_type_pred(cache_root: Path) -> dict:
    pred = {}
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
                    if not isinstance(api_detail, dict):
                        continue
                    pred_type = api_detail.get("type")
                    if isinstance(pred_type, str) and pred_type.strip():
                        pred[(project_dir.name, api_name.strip())] = pred_type.strip()
    return pred


def load_mapping_pred(cache_root: Path) -> dict:
    pred = {}
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
                replace_para = [str(x).strip() for x in replace_para] if isinstance(replace_para, list) else []
                cand = {"keep_pra": keep_pra, "replace_para": replace_para}
                for route in route_names:
                    if isinstance(route, str) and route.strip():
                        key = (project_dir.name, route.strip())
                        pred.setdefault(key, []).append(cand)
    return pred


def load_identifier_pred(cache_root: Path) -> dict[str, set[str]]:
    """
    按项目收集 identifier 参数预测集合，来源:
    - horizontal_results/container_reoust_id_result.json
    - horizontal_results/container_resource_divide_results.json
    - horizontal_results/data_resource_id_result.json
    """
    pred: dict[str, set[str]] = {}
    if not cache_root.exists():
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

    for project_dir in cache_root.iterdir():
        if not project_dir.is_dir():
            continue
        bucket: set[str] = set()
        hr = project_dir / "horizontal_results"
        for name in (
            "container_reoust_id_result.json",
            "container_resource_divide_results.json",
            "data_resource_id_result.json",
        ):
            fp = hr / name
            if not fp.exists():
                continue
            try:
                obj = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            add_from_obj(bucket, obj)
        pred[project_dir.name] = bucket
    return pred


def compute_common_metrics(total: int, found: int, correct: int) -> dict:
    """
    统一指标：
    - precision = correct / found
    - recall = correct / total
    - accuracy_on_gold 保留为兼容字段（等同 recall）
    """
    missing = total - found
    wrong = found - correct
    precision = (correct / found) if found else 0.0
    recall = (correct / total) if total else 0.0
    return {
        "total": total,
        "found": found,
        "correct": correct,
        "missing": missing,
        "wrong": wrong,
        "precision": precision,
        "recall": recall,
        "accuracy_on_gold": recall,
    }


def eval_type(gold_rows: list[dict], pred_rows: dict) -> dict:
    total = len(gold_rows)
    found = 0
    correct = 0
    for row in gold_rows:
        pred = pred_rows.get((row["project"], row["api_name"]))
        if pred is None:
            continue
        found += 1
        if pred == row["gold_type"]:
            correct += 1
    return compute_common_metrics(total=total, found=found, correct=correct)


def eval_mapping(gold_rows: list[dict], pred_rows: dict, model: str, debug_rows: list[dict]) -> dict:
    total = len(gold_rows)
    found = 0
    correct = 0
    for row in gold_rows:
        key = (row["project"], row["api_name"])
        candidates = pred_rows.get(key, [])
        if not candidates:
            continue
        found += 1
        gold_keep = set(x.strip() for x in row["gold_keep_pra"] if x and x.strip())
        gold_replace = set(x.strip() for x in row["gold_replace_para"] if x and x.strip())
        ok = False
        for cand in candidates:
            cand_keep = cand.get("keep_pra", "").strip()
            pred_replace = set(x.strip() for x in cand.get("replace_para", []) if x and x.strip())
            replace_hit = bool(pred_replace & gold_replace)
            keep_hit = (not gold_keep) or (cand_keep in gold_keep)
            # Manually marked false positives relax keep_pra strictness:
            # if replace_para hits gold_replace_para, treat as matched.
            if replace_hit and (keep_hit or row.get("is_false_positive", False)):
                ok = True
                break
        if ok:
            correct += 1
            continue

        mismatch_cands = (
            [c for c in candidates if c.get("keep_pra", "").strip() not in gold_keep] if gold_keep else []
        )
        if mismatch_cands:
            debug_rows.append(
                {
                    "model": model,
                    "project": row["project"],
                    "api_name": row["api_name"],
                    "parameter_name": row["parameter_name"],
                    "gold_keep_pra": json.dumps(sorted(gold_keep), ensure_ascii=False),
                    "gold_replace_para": json.dumps(sorted(gold_replace), ensure_ascii=False),
                    "pred_candidates_keep_pra": json.dumps(
                        sorted({c.get("keep_pra", "") for c in mismatch_cands}), ensure_ascii=False
                    ),
                    "pred_candidates_replace_para": json.dumps(
                        [c.get("replace_para", []) for c in mismatch_cands], ensure_ascii=False
                    ),
                }
            )

    return compute_common_metrics(total=total, found=found, correct=correct)


def eval_identifier(gold_rows: dict[str, dict[str, int]], pred_rows: dict[str, set[str]]) -> dict:
    total = 0
    tp = 0
    fp = 0
    fn = 0
    tn = 0
    predicted_total_all = 0

    for project in sorted(gold_rows.keys()):
        gold_map = gold_rows.get(project, {})
        pred_set = pred_rows.get(project, set())
        predicted_total_all += len(pred_set)
        for parameter, label in gold_map.items():
            total += 1
            pred_yes = parameter in pred_set
            gold_yes = int(label) == 1
            if gold_yes and pred_yes:
                tp += 1
            elif gold_yes and (not pred_yes):
                fn += 1
            elif (not gold_yes) and pred_yes:
                fp += 1
            else:
                tn += 1

    predicted_total_in_u = tp + fp
    precision = (tp / predicted_total_in_u) if predicted_total_in_u else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) else 0.0
    accuracy = ((tp + tn) / total) if total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "total": total,
        "found": predicted_total_in_u,
        "correct": tp,
        "missing": fn,
        "wrong": fp,
        "precision": precision,
        "recall": recall,
        "accuracy_on_gold": accuracy,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "predicted_total": predicted_total_in_u,
        "predicted_total_all": predicted_total_all,
    }


def write_debug_csv(fp: Path, rows: list[dict]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "model",
        "project",
        "api_name",
        "parameter_name",
        "gold_keep_pra",
        "gold_replace_para",
        "pred_candidates_keep_pra",
        "pred_candidates_replace_para",
    ]
    with fp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    gold_type = load_gold_type(root / args.gold_type)
    gold_map = load_gold_mapping(root / args.gold_mapping)
    gold_identifier = load_gold_identifier(root / args.gold_identifier)

    result = {}
    debug_rows = []
    for model in models:
        cache_root = root / f"cache_{model}"
        type_pred = load_type_pred(cache_root)
        mapping_pred = load_mapping_pred(cache_root)
        identifier_pred = load_identifier_pred(cache_root)
        result[model] = {
            "type_eval": eval_type(gold_type, type_pred),
            "mapping_eval": eval_mapping(gold_map, mapping_pred, model, debug_rows),
            "identifier_eval": eval_identifier(gold_identifier, identifier_pred),
        }

    out_json = root / args.out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] metrics -> {out_json}")

    if args.debug_keep_pra_mismatch:
        debug_fp = root / args.debug_out_csv
        write_debug_csv(debug_fp, debug_rows)
        print(f"[ok] keep_pra mismatch debug -> {debug_fp} (rows={len(debug_rows)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
