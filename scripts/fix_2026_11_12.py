#!/usr/bin/env python3
"""One-off data corrections of 2026-11-12 (logged in DATA_AUDIT.md).

Run once from the repo root:  python3 scripts/fix_2026_11_12.py
Then: audit_data.py, sync_index.py, audit_data.py.

Only two kinds of change, neither from memory:
  - country set from the venue's own coordinates (point-in-polygon against
    data/geo/ne_110m_countries.geojson), where the field was empty or wrong;
  - values that are impossible or junk are set to null, not replaced.
"""
import json, os
import openpyxl

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
XLSX = os.path.join(ROOT, "Stadia_database.xlsx")
COUNTRY = {  # slug -> country the coordinates fall in
    "bc-place-stadium-vancouver": "Canada", "bmo-field-toronto": "Canada", "saputo-stadium": "Canada",
    "baba-yara-sport-stadium": "Ghana", "cape-coast-sports-stadium": "Ghana", "tamale-stadium": "Ghana",
    "bingu-national-stadium": "Malawi",
    "est-dio-da-tundavala-lubango-angola": "Angola", "ombaka-national-stadium": "Angola",
    "pentecost-martyrs-stadium": "Democratic Republic of Congo",
}
NULL_YEAR = ["nilton-santos-olympic-stadium", "estadio-de-malabo", "city-ground-stadium",
             "the-gabba-stadium-brisbane"]          # 1565, 1827, 1836, 2031
NULL_CITY = ["bc-place-stadium-vancouver", "tamale-stadium", "senegal-national-wrestling-arena",
             "city-ground-stadium"]                 # "82", "Cambridge", Riyadh coordinates, Miami
# ----------------------------------------


def main():
    index = {e["slug"]: e for e in json.load(open(os.path.join(DATA, "index.json")))}
    touched = sorted(set(COUNTRY) | set(NULL_YEAR) | set(NULL_CITY))
    for slug in touched:
        # the canonical file plus any older pass of the same venue
        files = [slug] + [c["slug"] for c in index[slug].get("collections", []) if c["slug"] != slug]
        for fs in files:
            path = os.path.join(DATA, fs + ".json")
            d = json.load(open(path))
            m = d["m"]
            if slug in COUNTRY: m["country"] = COUNTRY[slug]
            if slug in NULL_YEAR: m["openingYear"] = None
            if slug in NULL_CITY: m["city"] = None
            with open(path, "w") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    wb = openpyxl.load_workbook(XLSX)
    ws = wb["stadiums"]
    col = {c.value: i + 1 for i, c in enumerate(ws[1])}
    n = 0
    for row in range(2, ws.max_row + 1):
        slug = ws.cell(row, col["slug"]).value
        if slug in COUNTRY: ws.cell(row, col["country"]).value = COUNTRY[slug]; n += 1
        if slug in NULL_YEAR: ws.cell(row, col["opening_year"]).value = None; n += 1
        if slug in NULL_CITY: ws.cell(row, col["city"]).value = None; n += 1
    wb.save(XLSX)
    print(f"{len(touched)} venues corrected in data/; {n} spreadsheet cells updated")


if __name__ == "__main__":
    main()
