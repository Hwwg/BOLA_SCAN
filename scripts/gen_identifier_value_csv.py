#!/usr/bin/env python3
import csv
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "cache_gpt-5.1"
OUT_FILE = ROOT / "scripts" / "identifier_value.csv"


def main() -> int:
    rows: list[tuple[str, str]] = []
    rng = random.Random()

    for proj in sorted([p for p in CACHE_ROOT.iterdir() if p.is_dir()]):
        src = proj / "horizontal_results" / "container_resource_divide_results.json"
        if not src.exists():
            continue

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            continue

        params: set[str] = set()
        for section in ("ou_id", "resource_id"):
            items = data.get(section, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                for values in item.values():
                    if isinstance(values, list):
                        for v in values:
                            if isinstance(v, str) and v.strip():
                                params.add(v.strip())

        if not params:
            continue

        for param in rng.sample(list(params), min(5, len(params))):
            rows.append((proj.name, param))

    with OUT_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["project", "parameter"])
        writer.writerows(rows)

    print(f"projects={len({r[0] for r in rows})}")
    print(f"rows={len(rows)}")
    print(str(OUT_FILE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
