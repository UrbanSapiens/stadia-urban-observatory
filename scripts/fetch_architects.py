#!/usr/bin/env python3
"""Architects of each venue's CURRENT building, from Wikidata (property P84).

Run from the repo root:  python3 scripts/fetch_architects.py
Out:  outputs/architects_wikidata.csv   (nothing else is modified)

The Wikidata item of each venue comes from the paper project's location match
(wikidata_venues.csv: the stadium item nearest the collected coordinates). That
item describes the building standing there now, so its P84 values are the
architects of the current version. Venues without a matched item, or whose
item has no P84, get no architect: nothing is filled from memory.
"""
import csv, json, os, re, subprocess, urllib.parse

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = ("/Users/g969d967/Library/CloudStorage/OneDrive-UniversityofKansas/Scripts/"
         "academic_publishing_system/lbsn-stadia-performance/data/first_wave_v2")
DATES = os.path.join(ROOT, "outputs", "collection_dates.csv")   # slug <-> workbook folder
OUT = os.path.join(ROOT, "outputs", "architects_wikidata.csv")
ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "venue-architect-lookup/1.0"
# ----------------------------------------


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    wd = {r["venue_id"]: r for r in csv.DictReader(open(os.path.join(PAPER, "wikidata_venues.csv"), encoding="utf-8"))}
    folder = {r["venue_id"]: r["wb_folder"] for r in csv.DictReader(open(os.path.join(PAPER, "venues.csv"), encoding="utf-8"))}
    slug_of = {key(r["workbook_folder"]): r["slug"] for r in csv.DictReader(open(DATES, encoding="utf-8"))}
    index = {e["slug"] for e in json.load(open(os.path.join(ROOT, "data", "index.json")))}
    items = {}
    for vid, r in wd.items():
        q, slug = r["wd_item"].strip(), slug_of.get(key(folder.get(vid, "")))
        if q.startswith("Q") and slug in index and not r.get("wd_flag", "").strip():
            items[q] = (slug, r["wd_label"])
    query = ("SELECT ?item ?aLabel WHERE { VALUES ?item { " + " ".join("wd:" + q for q in items) +
             " } ?item wdt:P84 ?a . SERVICE wikibase:label { bd:serviceParam wikibase:language 'en'. } }")
    raw = subprocess.run(["curl", "-s", "-G", ENDPOINT, "-H", "Accept: application/sparql-results+json",
                          "-A", USER_AGENT, "--data-urlencode", "query=" + query], capture_output=True, text=True, check=True).stdout
    found = {}
    for b in json.loads(raw)["results"]["bindings"]:
        q, name = b["item"]["value"].rsplit("/", 1)[-1], b["aLabel"]["value"]
        if not re.fullmatch(r"Q\d+", name):            # skip architects that have no English label
            found.setdefault(q, []).append(name)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "wikidata_item", "wikidata_label", "architects", "source"])
        for q, (slug, label) in sorted(items.items(), key=lambda kv: kv[1][0]):
            w.writerow([slug, q, label, "; ".join(sorted(set(found.get(q, [])))), f"https://www.wikidata.org/wiki/{q}#P84"])
    print(f"{len(items)} venues with a matched Wikidata item; {sum(1 for q in items if found.get(q))} have an architect there")
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
