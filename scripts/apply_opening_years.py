#!/usr/bin/env python3
"""Set each venue's year to the year its CURRENT building opened, from a checked table.

Run from the repo root:  python3 scripts/apply_opening_years.py
Then: audit_data.py, sync_index.py, build_site_data.py.
Out:  outputs/opening_years.csv  (every change, with the old value and the check)

Source: venue_history.csv of the paper project (159 venues; each year confirmed
against Wikidata or a Wikipedia infobox, or accepted by hand). Its year is the
opening of the current building. For a venue coded MAJOR_RENOVATION that year
is the reopening after the rebuild, so the site labels it "rebuilt", not
"opened" (openingYearKind). Venues outside that table are left as they are.
"""
import csv, json, os, re
import openpyxl

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
XLSX = os.path.join(ROOT, "Stadia_database.xlsx")
PAPER = ("/Users/g969d967/Library/CloudStorage/OneDrive-UniversityofKansas/Scripts/"
         "academic_publishing_system/lbsn-stadia-performance/data/first_wave_v2")
DATES = os.path.join(ROOT, "outputs", "collection_dates.csv")   # slug <-> workbook folder
OUT = os.path.join(ROOT, "outputs", "opening_years.csv")
REBUILT = {"MAJOR_RENOVATION"}
# ----------------------------------------


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    folder = {r["venue_id"]: r["wb_folder"] for r in csv.DictReader(open(os.path.join(PAPER, "venues.csv"), encoding="utf-8"))}
    slug_of = {key(r["workbook_folder"]): r["slug"] for r in csv.DictReader(open(DATES, encoding="utf-8"))}
    index = {e["slug"] for e in json.load(open(os.path.join(DATA, "index.json")))}
    log, new = [], {}
    for r in csv.DictReader(open(os.path.join(PAPER, "venue_history.csv"), encoding="utf-8")):
        slug = slug_of.get(key(folder.get(r["venue_id"], "")))
        if slug not in index or not r["current_opened"] or r["check"].startswith("DROPPED"):
            continue
        year = int(float(r["current_opened"]))
        kind = "rebuilt" if r["intervention"] in REBUILT else "opened"
        path = os.path.join(DATA, slug + ".json")
        doc = json.load(open(path))
        old = doc["m"].get("openingYear")
        doc["m"]["openingYear"], doc["m"]["openingYearKind"] = year, kind
        doc["m"]["openingYearSource"] = r["check"]
        with open(path, "w") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        new[slug] = year
        log.append([slug, old if old is not None else "", year, kind, r["intervention"], r["check"]])
    wb = openpyxl.load_workbook(XLSX)
    ws = wb["stadiums"]
    col = {c.value: i + 1 for i, c in enumerate(ws[1])}
    for row in range(2, ws.max_row + 1):
        s = ws.cell(row, col["slug"]).value
        if s in new:
            ws.cell(row, col["opening_year"]).value = new[s]
    wb.save(XLSX)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "old_year", "year", "kind", "intervention", "check"])
        w.writerows(sorted(log))
    filled = sum(1 for x in log if x[1] == "")
    changed = sum(1 for x in log if x[1] != "" and x[1] != x[2])
    print(f"{len(log)} venues set from the checked table: {filled} filled, {changed} corrected, "
          f"{len(log) - filled - changed} unchanged; {sum(1 for x in log if x[3] == 'rebuilt')} labelled rebuilt")
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
