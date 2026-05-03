#!/usr/bin/env python3
import argparse
import csv
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-model", default="gpt-4o-mini")
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--project", action="append")
    parser.add_argument(
        "--out-csv",
        default="scripts/manual_label_api_group_ground_truth.csv",
    )
    return parser.parse_args()


def normalize_model_dir(model: str) -> str:
    value = (model or "").strip()
    if not value:
        return "default"
    return value.replace("/", "_")


def resolve_projects(cache_dir: Path, project_args: list[str] | None) -> list[str]:
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


def collect_api_names(api_doc_data: list[dict]) -> list[str]:
    items: list[str] = []
    for group_obj in api_doc_data:
        if not isinstance(group_obj, dict):
            continue
        for _, group_value in group_obj.items():
            if not isinstance(group_value, dict):
                continue
            for api_name, api_detail in group_value.items():
                if api_name == "__inherited_params__":
                    continue
                if not isinstance(api_name, str):
                    continue
                if not isinstance(api_detail, dict):
                    continue
                s = api_name.strip()
                if s:
                    items.append(s)
    return items


def read_existing_rows(csv_fp: Path) -> dict[tuple[str, str], dict]:
    if not csv_fp.exists():
        return {}
    rows: dict[tuple[str, str], dict] = {}
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            project = (r.get("project") or "").strip()
            api_name = (r.get("api_name") or "").strip()
            if not project or not api_name:
                continue
            rows[(project, api_name)] = {
                "project": project,
                "api_name": api_name,
                "gold_group": (r.get("gold_group") or "").strip(),
                "note": (r.get("note") or "").strip(),
            }
    return rows


def write_rows(csv_fp: Path, rows: list[dict]) -> None:
    csv_fp.parent.mkdir(parents=True, exist_ok=True)
    with csv_fp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["project", "api_name", "gold_group", "note"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        raise ValueError("--count must be > 0")

    model_dir = normalize_model_dir(args.source_model)
    cache_dir = ROOT / f"cache_{model_dir}"
    if not cache_dir.exists():
        raise FileNotFoundError(str(cache_dir))

    rng = random.Random(args.seed)
    projects = resolve_projects(cache_dir, args.project)
    if not projects:
        print("[warn] no projects found")
        return 0

    out_csv = ROOT / args.out_csv
    existing = read_existing_rows(out_csv)
    merged = dict(existing)

    for project in projects:
        fp = cache_dir / project / "api_doc_with_type.json"
        if not fp.exists():
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue
        api_names = collect_api_names(data)
        if not api_names:
            continue
        sample_size = min(args.count, len(api_names))
        sampled = rng.sample(api_names, sample_size)
        for api_name in sampled:
            key = (project, api_name)
            if key not in merged:
                merged[key] = {
                    "project": project,
                    "api_name": api_name,
                    "gold_group": "",
                    "note": "",
                }

    out_rows = sorted(merged.values(), key=lambda x: (x["project"], x["api_name"]))
    write_rows(out_csv, out_rows)
    print(f"[ok] wrote {len(out_rows)} rows -> {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

