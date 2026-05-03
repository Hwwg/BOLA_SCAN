import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--in-csv", default="scripts/version_drift_gpt-4o-mini_table.csv")
    p.add_argument(
        "--versions",
        default="gpt-4o-mini,gpt-4o-mini_v2,gpt-4o-mini_v3,gpt-4o-mini_v4,gpt-4o-mini_v5",
    )
    p.add_argument(
        "--labels",
        default="v1,v2,v3,v4,v5",
    )
    p.add_argument("--out-html", default="scripts/version_drift_gpt-4o-mini.html")
    return p.parse_args()


def pct_to_float(s: str) -> float | None:
    if s is None:
        return None
    v = str(s).strip()
    if not v or v == "-":
        return None
    if v.endswith("%"):
        v = v[:-1].strip()
    try:
        return float(v)
    except Exception:
        return None


def load_csv(fp: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    with fp.open("r", encoding="utf-8", newline="") as f:
        first = f.readline()
    if first.startswith("version_label,") or first.startswith("version_label;") or first.startswith("version_label\t"):
        table: dict[str, dict[str, str]] = {}
        column_map = {
            "functional_group.prec": "FunctionalGroup_Prec",
            "functional_group.rec": "FunctionalGroup_Rec",
            "parameter_mapping.prec": "ParamMapping_Prec",
            "parameter_mapping.rec": "ParamMapping_Rec",
            "operation_semantics.prec": "OpSemantics_Prec",
            "identifier_recognition.prec": "Identifier_Prec",
            "identifier_recognition.rec": "Identifier_Rec",
            "cads_dependency.prec": "CADS_Prec",
            "cads_dependency.rec": "CADS_Rec",
            "overall.avg_prec": "Overall_Prec",
            "overall.avg_rec": "Overall_Rec",
        }
        with fp.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                model = (r.get("model") or "").strip()
                if not model:
                    continue
                table[model] = {k: (v or "").strip() for k, v in r.items()}
        return table, column_map

    with fp.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        rows = list(r)
    if len(rows) < 3:
        return {}, {}
    h1 = [c.strip() for c in rows[0]]
    h2 = [c.strip() for c in rows[1]]
    keys = []
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
        record = {}
        for k, v in zip(keys, row):
            record[k] = v
        data[model] = record
    return data, {}


def build_series(
    models: list[str], records: dict[str, dict[str, str]], key: str, column_map: dict[str, str]
) -> list[float | None]:
    out = []
    real_key = column_map.get(key, key)
    for m in models:
        rec = records.get(m, {})
        out.append(pct_to_float(rec.get(real_key, "")))
    return out


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent.parent
    in_fp = Path(args.in_csv)
    if not in_fp.is_absolute():
        candidate = base_dir / in_fp
        if candidate.exists():
            in_fp = candidate
    out_fp = Path(args.out_html)
    if not out_fp.is_absolute():
        out_fp = base_dir / out_fp
    models = [m.strip() for m in args.versions.split(",") if m.strip()]
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    if len(labels) != len(models):
        labels = models
    recs, column_map = load_csv(in_fp)

    tasks = [
        ("Functional Group", "functional_group.prec", "functional_group.rec"),
        ("Parameter Mapping", "parameter_mapping.prec", "parameter_mapping.rec"),
        ("Operation Semantics", "operation_semantics.prec", None),
        ("Identifier Recognition", "identifier_recognition.prec", "identifier_recognition.rec"),
        ("CADS Dependency", "cads_dependency.prec", "cads_dependency.rec"),
        ("Overall", "overall.avg_prec", "overall.avg_rec"),
    ]

    charts = []
    for title, prec_key, rec_key in tasks:
        if rec_key is None:
            charts.append(
                {
                    "title": title,
                    "kind": "acc",
                    "labels": labels,
                    "accuracy": build_series(models, recs, prec_key, column_map),
                }
            )
        else:
            charts.append(
                {
                    "title": title,
                    "kind": "prec_rec",
                    "labels": labels,
                    "precision": build_series(models, recs, prec_key, column_map),
                    "recall": build_series(models, recs, rec_key, column_map),
                }
            )

    out_fp.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(charts, ensure_ascii=False)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Version Drift</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, 'Noto Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; margin: 24px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; }}
    .title {{ font-weight: 600; margin: 0 0 10px 0; }}
    canvas {{ width: 100%; height: 260px; }}
  </style>
</head>
<body>
  <h2 style="margin:0 0 14px 0;">版本性能波动</h2>
  <div class="grid" id="grid"></div>
  <script>
    const charts = {payload};
    const grid = document.getElementById('grid');
    function mkCanvas(id) {{
      const card = document.createElement('div');
      card.className = 'card';
      const t = document.createElement('div');
      t.className = 'title';
      t.textContent = id;
      const c = document.createElement('canvas');
      card.appendChild(t);
      card.appendChild(c);
      grid.appendChild(card);
      return c;
    }}
    function mkAccLine(canvas, labels, title, accuracy) {{
      return new Chart(canvas, {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{
              label: 'Prec',
              data: accuracy,
              borderColor: '#2563eb',
              backgroundColor: 'rgba(37,99,235,0.1)',
              spanGaps: true,
              tension: 0.25
            }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ position: 'bottom' }},
            tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.dataset.label}}: ${{ctx.parsed.y?.toFixed(2)}}%` }} }}
          }},
          scales: {{
            y: {{
              min: 0,
              max: 100,
              ticks: {{ callback: (v) => `${{v}}%` }}
            }}
          }}
        }}
      }});
    }}
    function mkLine(canvas, labels, title, precision, recall) {{
      return new Chart(canvas, {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{
              label: 'Prec',
              data: precision,
              borderColor: '#2563eb',
              backgroundColor: 'rgba(37,99,235,0.1)',
              spanGaps: true,
              tension: 0.25
            }},
            {{
              label: 'Rec',
              data: recall,
              borderColor: '#dc2626',
              backgroundColor: 'rgba(220,38,38,0.1)',
              spanGaps: true,
              tension: 0.25
            }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ position: 'bottom' }},
            tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.dataset.label}}: ${{ctx.parsed.y?.toFixed(2)}}%` }} }}
          }},
          scales: {{
            y: {{
              min: 0,
              max: 100,
              ticks: {{ callback: (v) => `${{v}}%` }}
            }}
          }}
        }}
      }});
    }}
    for (const item of charts) {{
      const canvas = mkCanvas(item.title);
      if (item.kind === 'acc') {{
        mkAccLine(canvas, item.labels, item.title, item.accuracy);
      }} else {{
        mkLine(canvas, item.labels, item.title, item.precision, item.recall);
      }}
    }}
  </script>
</body>
</html>
"""
    out_fp.write_text(html, encoding="utf-8")
    print(f"[ok] wrote -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
