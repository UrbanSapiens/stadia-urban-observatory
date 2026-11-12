#!/usr/bin/env python3
"""Recover a collection date for each venue from the original workbooks.

Run from the repo root:  python3 scripts/extract_collection_dates.py
Out:  outputs/collection_dates.csv   (nothing else is modified)

Evidence used: the "created" timestamp stored inside each original master.xlsx
(docProps/core.xml). The collection pipeline writes a new workbook at the end
of each run, so "created" is the time of that run. It survives later re-saves
in Excel, which only change "modified". It does NOT survive a rewrite by
openpyxl, which is why the cleaned December 2025 copies all say 2025-12.
This is a file property, not a query log: treat it as the best available proxy.
"""
import csv, glob, os, re, zipfile
import openpyxl

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINALS = ("/Users/g969d967/Library/CloudStorage/OneDrive-UniversityofKansas/"
             "PhD_Dissertation/Data")          # 2020-21 workbooks, read-only
XLSX = os.path.join(ROOT, "Stadia_database.xlsx")
OUT = os.path.join(ROOT, "outputs", "collection_dates.csv")
# ----------------------------------------


def stamps(path):
    try:
        core = zipfile.ZipFile(path).read("docProps/core.xml").decode()
    except Exception as e:                       # cloud placeholder or damaged file
        return None, None, f"unreadable: {str(e)[:40]}"
    c = re.search(r"<dcterms:created[^>]*>([^<]+)", core)
    m = re.search(r"<dcterms:modified[^>]*>([^<]+)", core)
    return (c.group(1) if c else None), (m.group(1) if m else None), ""


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    books = {}
    for f in glob.glob(os.path.join(ORIGINALS, "**", "master.xlsx"), recursive=True):
        books.setdefault(key(os.path.basename(os.path.dirname(f))), []).append(f)
    ws = openpyxl.load_workbook(XLSX, read_only=True)["stadiums"]
    rows = list(ws.iter_rows(values_only=True))
    col = {h: i for i, h in enumerate(rows[0])}
    out, seen = [], set()
    for r in rows[1:]:
        slug, mp = r[col["slug"]], r[col["master_path"]] or ""
        if slug in seen:
            continue
        seen.add(slug)
        folder = mp.replace("\\", "/").rstrip("/").split("/")[-1]
        hits = books.get(key(folder), [])
        if len(hits) != 1:
            out.append([slug, r[col["name"]], folder, "", "", "no original workbook" if not hits else "ambiguous folder name"])
            continue
        c, m, note = stamps(hits[0])
        out.append([slug, r[col["name"]], folder, (c or "")[:10], (m or "")[:10], note])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "name", "workbook_folder", "workbook_created", "workbook_last_modified", "note"])
        w.writerows(sorted(out))
    dated = [o for o in out if o[3]]
    print(f"{len(out)} venues in the spreadsheet; {len(dated)} with a creation stamp; "
          f"{len(out) - len(dated)} without")
    print(f"range: {min(o[3] for o in dated)} to {max(o[3] for o in dated)}")
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
