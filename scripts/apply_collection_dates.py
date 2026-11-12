#!/usr/bin/env python3
"""Write the recovered collection dates into the data files and the index.

Run from the repo root, after extract_collection_dates.py:
    python3 scripts/apply_collection_dates.py
Then: sync_index.py and audit_data.py.

For each venue on the site, collectionDate becomes the creation stamp of its
original workbook (outputs/collection_dates.csv). Venues with no stamp get
null: no date is better than the December 2025 re-save date they carried.
"""
import csv, datetime, json, os

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DATES = os.path.join(ROOT, "outputs", "collection_dates.csv")
SOURCE_NOTE = "creation stamp of the original workbook"
# ----------------------------------------


def main():
    stamp = {r["slug"]: r["workbook_created"] for r in csv.DictReader(open(DATES, encoding="utf-8"))}
    index_path = os.path.join(DATA, "index.json")
    idx = json.load(open(index_path))
    dated = 0
    for e in idx:
        d = stamp.get(e["slug"]) or None
        label = datetime.date.fromisoformat(d).strftime("%B %Y") if d else None
        dated += bool(d)
        e["collectionDate"], e["collectionLabel"] = d, label
        path = os.path.join(DATA, e["slug"] + ".json")
        doc = json.load(open(path))
        doc["m"]["collectionDate"], doc["m"]["collectionLabel"] = d, label
        doc["m"]["collectionDateSource"] = SOURCE_NOTE if d else None
        with open(path, "w") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    with open(index_path, "w") as f:
        f.write("[\n" + ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in idx) + "\n]")
    print(f"{dated} of {len(idx)} venues dated; {len(idx) - dated} set to null")


if __name__ == "__main__":
    main()
