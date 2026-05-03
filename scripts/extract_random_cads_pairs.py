#!/usr/bin/env python3
import argparse
import csv
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-model", default="gemini-2.5-flash-preview-09-2025")
    parser.add_argument(
        "--downstream-source-model",
        default="gemini-2.5-flash-preview-09-2025",
        help="用于抽取 downstream_api 的参考模型（默认: gemini-2.5-flash-preview-09-2025）",
    )
    parser.add_argument("--count", type=int, default=10, help="每个项目抽取的 upstream API 数量（默认: 10）")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--project", action="append")
    parser.add_argument(
        "--append",
        action="store_true",
        help="追加到已存在的 CSV（默认覆盖重建）",
    )
    parser.add_argument(
        "--out-csv",
        default="scripts/manual_label_cads_dependency_pairs_ground_truth.csv",
    )
    return parser.parse_args()


def normalize_model_dir(model: str) -> str:
    value = (model or "").strip()
    if not value:
        return "default"
    return value.replace("/", "_")

def resolve_cache_dir(source_model: str) -> Path:
    token = normalize_model_dir(source_model)
    direct = ROOT / token
    if direct.exists() and direct.is_dir():
        return direct
    if token.startswith("cache_"):
        direct_cache = ROOT / token
        if direct_cache.exists() and direct_cache.is_dir():
            return direct_cache
        token = token[len("cache_") :]
    return ROOT / f"cache_{token}"


def resolve_projects(cache_dir: Path, project_args: list[str] | None) -> list[str]:
    all_projects = sorted(
        p.name
        for p in cache_dir.iterdir()
        if (p / "dependency_chains_results.json").exists()
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


def read_existing_keys(csv_fp: Path) -> set[tuple[str, str, str]]:
    if not csv_fp.exists():
        return set()
    keys: set[tuple[str, str, str]] = set()
    with csv_fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            project = (r.get("project") or "").strip()
            upstream = (r.get("upstream_api") or "").strip()
            downstream = (r.get("downstream_api") or "").strip()
            if project and upstream:
                keys.add((project, upstream, downstream))
    return keys


def load_project_chains(dep_fp: Path) -> list[list[str]]:
    obj = json.loads(dep_fp.read_text(encoding="utf-8"))
    chains: list[list[str]] = []
    for chain in iter_chain_dicts(obj):
        seq = [e for e in iter_endpoints(chain)]
        if seq:
            chains.append(seq)
    return chains


def build_candidate_pairs(chains: list[list[str]], rng: random.Random) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for seq in chains:
        prefix: list[str] = []
        seen: set[str] = set()
        for idx, ep in enumerate(seq):
            if not isinstance(ep, str) or not ep.strip():
                continue
            ep = ep.strip()
            if idx > 0 and prefix:
                upstream = rng.choice(prefix)
                if upstream != ep:
                    pairs.add((upstream, ep))
            if ep not in seen:
                seen.add(ep)
                prefix.append(ep)
    return list(pairs)


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        raise ValueError("--count must be > 0")

    rng = random.Random(args.seed)
    cache_dir = resolve_cache_dir(args.source_model)
    downstream_cache_dir = resolve_cache_dir(args.downstream_source_model)
    if not cache_dir.exists():
        raise FileNotFoundError(str(cache_dir))
    if not downstream_cache_dir.exists():
        raise FileNotFoundError(str(downstream_cache_dir))

    projects = resolve_projects(cache_dir, args.project)
    if not projects:
        print("[warn] no projects found")
        return 0

    out_fp = ROOT / args.out_csv
    rows: list[dict] = []
    existing: set[tuple[str, str, str]] = set()
    if args.append and out_fp.exists():
        with out_fp.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
        existing = read_existing_keys(out_fp)

    for project in projects:
        dep_fp = downstream_cache_dir / project / "dependency_chains_results.json"
        if not dep_fp.exists():
            dep_fp = cache_dir / project / "dependency_chains_results.json"
        if not dep_fp.exists():
            continue

        chains = load_project_chains(dep_fp)
        candidates = build_candidate_pairs(chains, rng)
        if not candidates:
            continue
        rng.shuffle(candidates)

        added = 0
        for upstream, downstream in candidates:
            if added >= args.count:
                break
            k = (project, upstream, downstream)
            if k in existing:
                continue
            rows.append(
                {
                    "project": project,
                    "upstream_api": upstream,
                    "downstream_api": downstream,
                    "note": "",
                }
            )
            existing.add(k)
            added += 1

    out_fp.parent.mkdir(parents=True, exist_ok=True)
    with out_fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["project", "upstream_api", "downstream_api", "note"],
        )
        w.writeheader()
        w.writerows(rows)

    print(f"[ok] wrote {len(rows)} rows -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
