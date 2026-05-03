#!/usr/bin/env python3
import argparse
import csv
import json
import random
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从多个模型的 identifier 预测集合取 union，并按 overlap 分层抽样生成封闭集标注 CSV"
    )
    parser.add_argument(
        "--models",
        default="gpt-4o-mini,gpt-5.1,deepseek-chat",
        help="模型列表，逗号分隔（默认: gpt-4o-mini,gpt-5.1,deepseek-chat）",
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="项目根目录",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=200,
        help="总抽样数量（默认: 200）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=2026,
        help="随机种子（默认: 2026）",
    )
    parser.add_argument(
        "--out-csv",
        default="scripts/identifier_closed_set_union_sample.csv",
        help="输出 CSV 路径（相对 root）",
    )
    return parser.parse_args()


def sanitize_model_name(model_name: str) -> str:
    import re

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", (model_name or "").strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "unknown_model"


def load_identifier_pred(cache_root: Path) -> dict[str, set[str]]:
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


def pattern_key(mask: int, model_count: int) -> str:
    bits = bin(mask)[2:].zfill(model_count)
    return bits


def mask_to_models(mask: int, models: list[str]) -> list[str]:
    hit = []
    for idx, m in enumerate(models):
        if mask & (1 << idx):
            hit.append(m)
    return hit


def allocate_counts(strata_sizes: dict[int, int], sample_size: int) -> dict[int, int]:
    non_empty = {k: v for k, v in strata_sizes.items() if v > 0}
    if not non_empty or sample_size <= 0:
        return {k: 0 for k in strata_sizes}
    total = sum(non_empty.values())
    alloc = {k: 0 for k in strata_sizes}
    raw = {k: (v / total) * sample_size for k, v in non_empty.items()}
    base = {k: int(raw[k]) for k in raw}
    remainder = sample_size - sum(base.values())
    alloc.update(base)
    if remainder > 0:
        frac = sorted(
            ((k, raw[k] - base[k]) for k in raw),
            key=lambda x: x[1],
            reverse=True,
        )
        for k, _ in frac:
            if remainder <= 0:
                break
            alloc[k] += 1
            remainder -= 1
    for k in list(alloc.keys()):
        alloc[k] = min(alloc[k], strata_sizes.get(k, 0))
    return alloc


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        raise ValueError("--models 至少需要 2 个模型")
    if args.sample_size <= 0:
        raise ValueError("--sample-size 必须大于 0")

    rng = random.Random(args.seed)

    model_preds: list[dict[str, set[str]]] = []
    for model in models:
        cache_root = root / f"cache_{sanitize_model_name(model)}"
        model_preds.append(load_identifier_pred(cache_root))

    union: dict[tuple[str, str], int] = {}
    all_projects = sorted({p for d in model_preds for p in d.keys()})
    for project in all_projects:
        for idx, pred in enumerate(model_preds):
            for param in pred.get(project, set()):
                key = (project, param)
                union[key] = union.get(key, 0) | (1 << idx)

    if not union:
        raise ValueError("未在给定模型的 cache 中找到任何 identifier 预测结果")

    strata: dict[int, list[tuple[str, str]]] = {}
    for key, mask in union.items():
        strata.setdefault(mask, []).append(key)

    strata_sizes = {k: len(v) for k, v in strata.items()}
    alloc = allocate_counts(strata_sizes, args.sample_size)

    sampled: list[tuple[str, str, int]] = []
    for mask, items in strata.items():
        take = alloc.get(mask, 0)
        if take <= 0:
            continue
        rng.shuffle(items)
        for project, param in items[:take]:
            sampled.append((project, param, mask))

    rng.shuffle(sampled)

    out_fp = root / args.out_csv
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    with out_fp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "project",
                "parameter",
                "label",
                "hit_models",
                "hit_pattern",
                "note",
            ],
        )
        writer.writeheader()
        for project, param, mask in sampled:
            hit = mask_to_models(mask, models)
            writer.writerow(
                {
                    "project": project,
                    "parameter": param,
                    "label": "",
                    "hit_models": "|".join(hit),
                    "hit_pattern": pattern_key(mask=mask, model_count=len(models)),
                    "note": "",
                }
            )

    print(f"[ok] sampled={len(sampled)} union={len(union)} -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
