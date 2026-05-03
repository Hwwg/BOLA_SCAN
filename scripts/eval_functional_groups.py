#!/usr/bin/env python3
import argparse
import csv
import json
from itertools import combinations
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gpt-4o-mini,gpt-5-mini,deepseek-chat,qwen3.6-flash-2026-04-16,gemini-2.5-flash-preview-09-2025")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--gold-groups",
        default="scripts/manual_label_api_group_ground_truth.csv",
    )
    parser.add_argument(
        "--out-json",
        default="scripts/eval_functional_groups_result.json",
    )
    return parser.parse_args()


def load_gold_groups(csv_fp: Path) -> dict[str, dict[str, str]]:
    gold: dict[str, dict[str, str]] = {}
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            project = (r.get("project") or "").strip()
            api_name = (r.get("api_name") or "").strip()
            group = (r.get("gold_group") or "").strip()
            if not project or not api_name:
                continue
            if not group:
                continue
            gold.setdefault(project, {})[api_name] = group
    return gold


def load_pred_groups(cache_root: Path, projects: set[str]) -> dict[tuple[str, str], str]:
    pred: dict[tuple[str, str], str] = {}
    if not cache_root.exists():
        return pred
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
                    if not isinstance(api_name, str):
                        continue
                    if not isinstance(api_detail, dict):
                        continue
                    k = (project, api_name.strip())
                    if k not in pred:
                        pred[k] = g
    return pred


def compute_pairwise_metrics(api_to_gold: dict[str, str], api_to_pred: dict[str, str]) -> dict:
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

    tp = 0
    fp = 0
    fn = 0
    for a, b in combinations(apis, 2):
        gold_same = api_to_gold.get(a) == api_to_gold.get(b)
        pred_same = api_to_pred.get(a, f"__missing__:{a}") == api_to_pred.get(b, f"__missing__:{b}")
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


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    gold_fp = root / args.gold_groups
    gold = load_gold_groups(gold_fp)
    if not gold:
        raise ValueError(f"no labeled rows found in {gold_fp}")

    projects = set(gold.keys())
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    result = {}
    for model in models:
        cache_root = root / f"cache_{model}"
        pred = load_pred_groups(cache_root, projects)
        per_project = {}
        total_tp = total_fp = total_fn = total_pairs = total_apis = 0
        for project, api_to_gold in gold.items():
            api_to_pred = {api: pred.get((project, api)) for api in api_to_gold.keys()}
            metrics = compute_pairwise_metrics(api_to_gold, api_to_pred)
            per_project[project] = metrics
            total_tp += metrics["tp"]
            total_fp += metrics["fp"]
            total_fn += metrics["fn"]
            total_pairs += metrics["pairs"]
            total_apis += metrics["apis"]

        precision = (total_tp / (total_tp + total_fp)) if (total_tp + total_fp) else 0.0
        recall = (total_tp / (total_tp + total_fn)) if (total_tp + total_fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        result[model] = {
            "overall": {
                "projects": len(per_project),
                "apis": total_apis,
                "pairs": total_pairs,
                "tp": total_tp,
                "fp": total_fp,
                "fn": total_fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            },
            "per_project": per_project,
        }

    out_fp = root / args.out_json
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    out_fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] functional group metrics -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
