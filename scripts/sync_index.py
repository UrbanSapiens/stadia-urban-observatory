#!/usr/bin/env python3
"""Propagate per-stadium data into data/index.json and index.html.

Run from the repo root:  python3 scripts/sync_index.py

The per-stadium files data/<slug>.json are the single source of truth for
stadium identity (name, city, country, capacity, openingYear, primarySport,
events). This script:

  1. copies those fields from each stadium file's "m" block into the matching
     entry of data/index.json (derived metrics like diversity/medianDist are
     left untouched — they come from the collection pipeline);
  2. re-derives the index entry's top-level "event"/"year" label from the
     events dict (tournaments take priority over leagues);
  3. rewrites the `const IX=[...]` array embedded in index.html so the live
     site matches — the site does NOT fetch data/index.json at runtime.

Workflow: edit data/<slug>.json → run this script → commit all three.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
HTML = os.path.join(ROOT, "index.html")

IDENTITY_FIELDS = ["name", "city", "country", "capacity", "openingYear", "primarySport"]

EVENT_LABELS = [  # priority order
    ("fifaWorldCup", "FIFA World Cup", "fifaWorldCupYear"),
    ("olympicGames", "Olympic Games", "olympicGamesYear"),
    ("uefaEuroCup", "UEFA Euro Cup", "uefaEuroCupYear"),
    ("mls", "MLS", None),
    ("nfl", "NFL", None),
    ("mlb", "MLB", None),
    ("nwsl", "NWSL", None),
    ("chinaStadiumDiplomacy", "China Stadium Diplomacy", None),
]


def derive_event(events):
    for key, label, ykey in EVENT_LABELS:
        if events.get(key):
            year = str(events.get(ykey, "")).replace(".0", "") if ykey else ""
            return label, year
    return "", ""


def main():
    index_path = os.path.join(DATA, "index.json")
    idx = json.load(open(index_path))
    changed = []

    for entry in idx:
        slug = entry["slug"]
        path = os.path.join(DATA, slug + ".json")
        if not os.path.exists(path):
            print(f"!! no stadium file for {slug} — skipped", file=sys.stderr)
            continue
        m = json.load(open(path))["m"]
        before = json.dumps(entry, sort_keys=True)
        for f in IDENTITY_FIELDS:
            if f in m:
                entry[f] = m[f]
        entry["events"] = m.get("events") or {}
        entry["event"], entry["year"] = derive_event(entry["events"])
        if json.dumps(entry, sort_keys=True) != before:
            changed.append(slug)

    with open(index_path, "w") as f:
        f.write("[\n" + ",\n".join(
            json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in idx) + "\n]")

    html = open(HTML).read()
    ix_block = "const IX=[\n" + ",\n".join(
        json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in idx) + "\n];"
    new_html, n = re.subn(r"const IX=\[\n.*?\n\];", lambda _: ix_block, html,
                          count=1, flags=re.S)
    if n != 1:
        print("!! could not locate the IX array in index.html — not updated", file=sys.stderr)
        return 1
    open(HTML, "w").write(new_html)

    print(f"{len(idx)} entries synced; {len(changed)} changed: {', '.join(changed) or '—'}")
    print("updated data/index.json and the IX array in index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
