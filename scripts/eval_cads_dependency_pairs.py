#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default="gpt-4o-mini,gpt-5-mini,deepseek-chat,qwen3.6-flash-2026-04-16,gemini-2.5-flash-preview-09-2025",
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--gold-csv",
        default="scripts/manual_label_cads_dependency_pairs_ground_truth.csv",
    )
    parser.add_argument(
        "--out-json",
        default="scripts/eval_cads_dependency_pairs_result.json",
    )
    parser.add_argument(
        "--out-summary-csv",
        default="scripts/eval_cads_dependency_pairs_summary.csv",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--debug-out-dir",
        default="scripts/eval_debug_cases_cads",
    )
    return parser.parse_args()


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


def load_gold(csv_fp: Path) -> list[dict]:
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


def iter_endpoints(obj):
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            yield s
        return
    if isinstance(obj, list):
        for x in obj:
            yield from iter_endpoints(x)
        return
    if isinstance(obj, dict):
        items = list(obj.items())
        if all(isinstance(k, str) and k.isdigit() for k, _ in items):
            items.sort(key=lambda kv: int(kv[0]))
        for _, v in items:
            yield from iter_endpoints(v)


def flatten_chain(chain_dict: dict) -> list[str]:
    return [e for e in iter_endpoints(chain_dict)]


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


def load_project_chains(dep_fp: Path) -> list[list[str]]:
    try:
        obj = json.loads(dep_fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    chains = []
    for chain in iter_chain_dicts(obj):
        seq = flatten_chain(chain)
        if seq:
            chains.append(seq)
    return chains


def write_csv(fp: Path, fieldnames: list[str], rows: list[dict]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    gold_rows = load_gold(root / args.gold_csv)
    if not gold_rows:
        raise ValueError("gold csv has no valid labeled rows")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    result = {}
    summary_rows: list[dict] = []

    for model in models:
        cache_root = resolve_cache_dir(root, model)
        total = len(gold_rows)
        found = 0
        correct = 0
        debug_rows: list[dict] = []

        chains_by_project: dict[str, list[list[str]]] = {}
        for row in gold_rows:
            project = row["project"]
            if project in chains_by_project:
                continue
            dep_fp = cache_root / project / "dependency_chains_results.json"
            if dep_fp.exists():
                chains_by_project[project] = load_project_chains(dep_fp)
            else:
                chains_by_project[project] = []

        for row in gold_rows:
            project = row["project"]
            upstream = row["upstream_api"]
            downstream = row["downstream_api"]
            chains = chains_by_project.get(project, [])
            if chains:
                found += 1
            ok = relation_exists(chains, upstream, downstream) if chains else False
            if ok:
                correct += 1
            elif args.debug:
                debug_rows.append(
                    {
                        "project": project,
                        "upstream_api": upstream,
                        "downstream_api": downstream,
                        "error_type": "unmatched" if chains else "missing_project_file",
                    }
                )

        precision = (correct / found) if found else 0.0
        recall = (correct / total) if total else 0.0
        result[model] = {"total": total, "found": found, "correct": correct, "precision": precision, "recall": recall}
        summary_rows.append(
            {
                "model": model,
                "total": total,
                "found": found,
                "correct": correct,
                "precision": f"{precision * 100:.2f}%",
                "recall": f"{recall * 100:.2f}%",
            }
        )

        if args.debug:
            out_dir = root / args.debug_out_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            write_csv(
                out_dir / f"{normalize_model_dir(model)}_cads_dependency_pair_mismatches.csv",
                ["project", "upstream_api", "downstream_api", "error_type"],
                debug_rows,
            )

    out_json = root / args.out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    out_summary = root / args.out_summary_csv
    write_csv(
        out_summary,
        ["model", "total", "found", "correct", "precision", "recall"],
        summary_rows,
    )
    print(f"[ok] cads metrics -> {out_json}")
    print(f"[ok] cads summary -> {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
