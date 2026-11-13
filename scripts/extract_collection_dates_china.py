#!/usr/bin/env python3
"""Recover a collection date for the Chinese diplomacy venues from their folders.

Run from the repo root:  python3 scripts/extract_collection_dates_china.py
Out:  outputs/collection_dates_china.csv   (nothing else is modified)

Why a second script: every master.xlsx in this folder was rewritten by openpyxl
on 2024-09-28, so the internal "created" stamp used for the other venues is
lost. The collection run also wrote the .osm download and error_log.txt, and
nothing rewrote those. Their file-system modification date is used instead.
This is weaker evidence than an internal stamp (a copy or a sync can change
it), so the site shows the month only.
"""
import csv, datetime, glob, os, re
import openpyxl

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDERS = ("/Users/g969d967/Library/CloudStorage/OneDrive-UniversityofKansas/"
           "Chinese Diplomacy Stadiums")        # read-only
RUN_FILES = ("*.osm", "error_log.txt")          # written by the collection run itself
XLSX = os.path.join(ROOT, "Stadia_database.xlsx")
OUT = os.path.join(ROOT, "outputs", "collection_dates_china.csv")
# ----------------------------------------


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    folders = {key(d): os.path.join(FOLDERS, d) for d in os.listdir(FOLDERS)
               if os.path.isdir(os.path.join(FOLDERS, d))}
    ws = openpyxl.load_workbook(XLSX, read_only=True)["stadiums"]
    rows = list(ws.iter_rows(values_only=True))
    col = {h: i for i, h in enumerate(rows[0])}
    out, seen = [], set()
    for r in rows[1:]:
        slug, mp = r[col["slug"]], (r[col["master_path"]] or "").replace("\\", "/")
        folder = mp.rstrip("/").split("/")[-1]
        if slug in seen or key(folder) not in folders:
            continue
        seen.add(slug)
        files = [f for pat in RUN_FILES for f in glob.glob(os.path.join(glob.escape(folders[key(folder)]), pat))]
        if not files:
            out.append([slug, folder, "", "", "no .osm or error_log.txt"])
            continue
        first = min(files, key=os.path.getmtime)
        day = datetime.date.fromtimestamp(os.path.getmtime(first)).isoformat()
        out.append([slug, folder, day, os.path.basename(first), ""])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "workbook_folder", "oldest_run_file_date", "file", "note"])
        w.writerows(sorted(out))
    dated = [o for o in out if o[2]]
    print(f"{len(out)} venues matched to a folder; {len(dated)} dated")
    if dated:
        print(f"range: {min(o[2] for o in dated)} to {max(o[2] for o in dated)}")
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
