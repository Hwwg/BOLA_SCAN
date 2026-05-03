import csv
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


NS_MAIN = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_PKGREL = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def col_to_idx(col: str) -> int:
    idx = 0
    for ch in col:
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: list[str] = []
    for si in root.findall("m:si", NS_MAIN):
        out.append("".join([(t.text or "") for t in si.findall(".//m:t", NS_MAIN)]))
    return out


def sheet_name_to_path(zf: zipfile.ZipFile) -> dict[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

    rid_to_target: dict[str, str] = {}
    for rel in rels.findall("r:Relationship", NS_PKGREL):
        rid_to_target[rel.attrib["Id"]] = rel.attrib["Target"]

    mapping: dict[str, str] = {}
    for s in wb.findall("m:sheets/m:sheet", NS_MAIN):
        name = s.attrib.get("name") or ""
        rid = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") or ""
        target = rid_to_target.get(rid, "")
        if target and not target.startswith("/"):
            target = "xl/" + target
        mapping[name] = target
    return mapping


def read_sheet_rows(zf: zipfile.ZipFile, shared: list[str], sheet_path: str) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", NS_MAIN):
        vals: dict[int, str] = {}
        for c in row.findall("m:c", NS_MAIN):
            ref = c.attrib.get("r", "")
            col = "".join(ch for ch in ref if ch.isalpha())
            if not col:
                continue
            idx = col_to_idx(col)
            t = c.attrib.get("t")
            v_el = c.find("m:v", NS_MAIN)
            if v_el is None:
                continue
            raw = v_el.text or ""
            if t == "s":
                val = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else raw
            elif t == "b":
                val = "TRUE" if raw == "1" else "FALSE"
            else:
                val = raw
            vals[idx] = val
        if not vals:
            continue
        max_c = max(vals)
        row_list = [""] * (max_c + 1)
        for i, v in vals.items():
            row_list[i] = v
        rows.append(row_list)
    return rows


PROBE_MULTI_BY_ID: dict[int, tuple[str, str]] = {
    1: ("RIC", "N"),
    2: ("CIC", "Y"),
    3: ("RIC", "Y"),
    4: ("RIC", "N"),
    5: ("RIC", "Y"),
    6: ("RIC", "Y"),
    7: ("RIC", "Y"),
    8: ("RIC", "Y"),
    9: ("RIC", "N"),
    10: ("RIC", "Y"),
    11: ("RIC", "Y"),
    12: ("RIC", "Y"),
    13: ("RIC", "Y"),
    14: ("RIC", "Y"),
    15: ("RIC", "N"),
    16: ("CIC", "Y"),
    17: ("RIC", "Y"),
    18: ("RIC", "N"),
    19: ("RIC", "Y"),
    20: ("RIC", "Y"),
    21: ("RIC", "N"),
    22: ("RIC", "N"),
    23: ("RIC", "Y"),
    24: ("RIC", "N"),
    25: ("RIC", "N"),
    26: ("RIC", "Y"),
    27: ("RIC", "Y"),
    28: ("RIC", "Y"),
    29: ("RIC", "Y"),
    30: ("CIC", "N"),
    31: ("CIC", "Y"),
    32: ("CIC", "Y"),
    33: ("CIC", "Y"),
    34: ("CIC", "Y"),
    35: ("CIC", "N"),
    36: ("CIC", "N"),
    37: ("CIC", "N"),
    38: ("CIC", "Y"),
    39: ("CIC", "Y"),
    40: ("CIC", "N"),
    41: ("CIC", "N"),
    42: ("CIC", "N"),
    43: ("RIC", "N"),
    44: ("RIC", "Y"),
    45: ("RIC", "Y"),
    46: ("RIC", "N"),
    47: ("RIC", "N"),
    48: ("RIC", "Y"),
    49: ("RIC", "Y"),
    50: ("RIC", "Y"),
    51: ("RIC", "Y"),
    52: ("RIC", "Y"),
    53: ("RIC", "Y"),
    54: ("RIC", "Y"),
    55: ("RIC", "Y"),
    56: ("RIC", "Y"),
}


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    xlsx_fp = script_dir / "实验数据(2).xlsx"
    out_fp = script_dir / "sheet3_with_probe_multiid.csv"

    with zipfile.ZipFile(xlsx_fp) as zf:
        shared = read_shared_strings(zf)
        mapping = sheet_name_to_path(zf)
        sheet_path = mapping.get("Sheet3")
        if not sheet_path:
            raise SystemExit(f"Sheet3 not found. available={sorted(mapping.keys())}")
        rows = read_sheet_rows(zf, shared, sheet_path)

    if not rows:
        raise SystemExit("Sheet3 is empty")

    headers = [c.strip() for c in rows[0]]
    id_idx = headers.index("id") if "id" in headers else 0

    out_headers = [*headers, "Probe", "Multi-ID"]

    out_fp.parent.mkdir(parents=True, exist_ok=True)
    missing: list[int] = []
    with out_fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_headers)
        for r in rows[1:]:
            row = r + [""] * (len(headers) - len(r))
            raw_id = (row[id_idx] or "").strip()
            try:
                rid = int(raw_id)
            except Exception:
                rid = None
            probe = ""
            multi = ""
            if rid is not None:
                pm = PROBE_MULTI_BY_ID.get(rid)
                if pm is None:
                    missing.append(rid)
                else:
                    probe, multi = pm
            w.writerow([*row[: len(headers)], probe, multi])

    if missing:
        print("[warn] missing Probe/Multi-ID for ids:", sorted(set(missing)))
    print(f"[ok] wrote -> {out_fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
