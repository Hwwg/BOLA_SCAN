import argparse
import csv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--in-overview-csv", default="scripts/eval_all_experiments_overview.csv")
    p.add_argument(
        "--versions",
        default="gpt-4o-mini,gpt-4o-mini_v2,gpt-4o-mini_v3,gpt-4o-mini_v4,gpt-4o-mini_v5",
    )
    p.add_argument("--labels", default="v1,v2,v3,v4,v5")
    p.add_argument("--out-csv", default="scripts/version_drift_gpt-4o-mini_table.csv")
    return p.parse_args()


def load_overview_csv(fp: Path) -> dict[str, dict[str, str]]:
    with fp.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        rows = list(r)
    if len(rows) < 3:
        return {}
    h1 = [c.strip() for c in rows[0]]
    h2 = [c.strip() for c in rows[1]]
    keys: list[str] = []
    for a, b in zip(h1, h2):
        if a and b:
            keys.append(f"{a}.{b}")
        elif b:
            keys.append(b)
        else:
            keys.append(a)
    data: dict[str, dict[str, str]] = {}
    for row in rows[2:]:
        if not row:
            continue
        model = (row[0] or "").strip()
        if not model or model == "AVG(models)":
            continue
        record: dict[str, str] = {}
        for k, v in zip(keys, row):
            record[k] = v
        data[model] = record
    return data


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent.parent
    in_fp = Path(args.in_overview_csv)
    if not in_fp.is_absolute():
        candidate = base_dir / in_fp
        if candidate.exists():
            in_fp = candidate
    out_fp = Path(args.out_csv)
    if not out_fp.is_absolute():
        out_fp = base_dir / out_fp
    models = [m.strip() for m in args.versions.split(",") if m.strip()]
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    if len(labels) != len(models):
        labels = models

    recs = load_overview_csv(in_fp)

    fields = [
        ("FunctionalGroup_Prec", "functional_group.prec"),
        ("FunctionalGroup_Rec", "functional_group.rec"),
        ("ParamMapping_Prec", "parameter_mapping.prec"),
        ("ParamMapping_Rec", "parameter_mapping.rec"),
        ("OpSemantics_Prec", "operation_semantics.prec"),
        ("Identifier_Prec", "identifier_recognition.prec"),
        ("Identifier_Rec", "identifier_recognition.rec"),
        ("CADS_Prec", "cads_dependency.prec"),
        ("CADS_Rec", "cads_dependency.rec"),
        ("Overall_Prec", "overall.avg_prec"),
        ("Overall_Rec", "overall.avg_rec"),
    ]

    out_fp.parent.mkdir(parents=True, exist_ok=True)
    with out_fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["version_label", "model", *[name for name, _ in fields]])
        for label, model in zip(labels, models):
            row = [label, model]
            rec = recs.get(model, {})
            for _, key in fields:
                row.append(rec.get(key, "-") or "-")
            w.writerow(row)

    print(f"[ok] wrote -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
