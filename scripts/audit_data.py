#!/usr/bin/env python3
"""Data accuracy audit for the Stadia Urban Observatory dataset.

Run from the repo root:  python3 scripts/audit_data.py

Checks every stadium in data/index.json against ground truth and internal
consistency rules, and prints a report of anything suspicious. Run this after
every data collection / before every publish. Nothing is modified; fix the
flagged files by hand (or with your pipeline), then run scripts/sync_index.py
to propagate the fixes into data/index.json and the IX array in index.html.

Checks performed:
  1. Event claims vs tournament ground truth
     - country-level: the claimed year's tournament must have been hosted in
       the stadium's country (all WC / Euro / Olympic years).
     - venue-level: for WC 2006/2010/2014/2018 the stadium must be within
       70 km of an actual host city (catches e.g. Goiânia claiming WC 2014).
  2. Stadiums with no recorded event (why are they in the study?).
  3. Duplicate venues: two entries within 300 m of each other.
  4. builtArea outside (0, 1] — 36.0 is a known placeholder value.
  5. Names that look like raw coordinates.
  6. City fields that look scrambled (contain coordinates, or repeat the country).
  7. index.json entries vs per-stadium data/<slug>.json m-block consistency.
"""
import json, math, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

WC_HOSTS = {
    "1930": ["Uruguay"], "1934": ["Italy"], "1938": ["France"], "1950": ["Brazil"],
    "1954": ["Switzerland"], "1958": ["Sweden"], "1962": ["Chile"],
    "1966": ["England", "United Kingdom"], "1970": ["Mexico"], "1974": ["Germany"],
    "1978": ["Argentina"], "1982": ["Spain"], "1986": ["Mexico"], "1990": ["Italy"],
    "1994": ["United States"], "1998": ["France"],
    "2002": ["South Korea", "Korea Republic", "Japan"], "2006": ["Germany"],
    "2010": ["South Africa"], "2014": ["Brazil"], "2018": ["Russia"],
    "2022": ["Qatar"], "2026": ["United States", "Canada", "Mexico"],
}
EURO_HOSTS = {
    "1960": ["France"], "1964": ["Spain"], "1968": ["Italy"], "1972": ["Belgium"],
    "1976": ["Yugoslavia"], "1980": ["Italy"], "1984": ["France"],
    "1988": ["Germany", "West Germany"], "1992": ["Sweden"],
    "1996": ["England", "United Kingdom"], "2000": ["Netherlands", "Belgium"],
    "2004": ["Portugal"], "2008": ["Austria", "Switzerland"],
    "2012": ["Poland", "Ukraine"], "2016": ["France"],
    "2020": ["England", "United Kingdom", "Italy", "Spain", "Germany", "Hungary",
             "Netherlands", "Denmark", "Scotland", "Romania", "Azerbaijan", "Russia"],
    "2024": ["Germany"],
}
OLY_HOSTS = {
    "1992": ["Spain"], "1996": ["United States"], "2000": ["Australia"],
    "2004": ["Greece"], "2008": ["China"], "2012": ["United Kingdom", "England"],
    "2016": ["Brazil"], "2020": ["Japan"], "2024": ["France"],
}
# Host cities (lat, lng) for venue-level WC checks. A legitimate host stadium
# must lie within HOST_CITY_KM of one of its tournament's cities.
WC_CITIES = {
    "2006": [(52.51, 13.40), (48.14, 11.58), (51.51, 7.47), (51.52, 7.06),
             (48.78, 9.18), (53.55, 9.99), (50.11, 8.68), (50.94, 6.96),
             (52.37, 9.73), (51.34, 12.37), (49.44, 7.77), (49.45, 11.08)],
    "2010": [(-26.20, 28.05), (-33.92, 18.42), (-29.85, 31.02), (-25.75, 28.22),
             (-33.94, 25.60), (-29.12, 26.21), (-25.58, 27.16), (-23.92, 29.47),
             (-25.46, 30.93)],
    "2014": [(-22.91, -43.23), (-23.55, -46.47), (-15.78, -47.90), (-19.87, -43.97),
             (-3.81, -38.52), (-12.98, -38.50), (-8.04, -35.01), (-5.83, -35.21),
             (-3.08, -60.03), (-15.60, -56.12), (-25.45, -49.28), (-30.07, -51.24)],
    "2018": [(55.75, 37.60), (59.97, 30.22), (43.40, 39.95), (55.82, 49.16),
             (53.28, 50.24), (54.18, 45.18), (54.70, 20.53), (48.78, 44.55),
             (56.34, 43.96), (47.21, 39.74), (56.83, 60.57)],
}
HOST_CITY_KM = 70


def km(lat1, lng1, lat2, lng2):
    return math.hypot((lat1 - lat2) * 111.32,
                      (lng1 - lng2) * 111.32 * math.cos(math.radians(lat1)))


def year_of(ev, key):
    return str(ev.get(key, "")).replace(".0", "").strip()


def main():
    idx = json.load(open(os.path.join(DATA, "index.json")))
    flags = []

    def flag(sev, slug, msg):
        flags.append((sev, slug, msg))

    for s in idx:
        slug, country = s["slug"], s.get("country")
        ev = s.get("events") or {}

        # 1. event ground truth
        for evkey, ykey, hosts, label in (
            ("fifaWorldCup", "fifaWorldCupYear", WC_HOSTS, "FIFA WC"),
            ("uefaEuroCup", "uefaEuroCupYear", EURO_HOSTS, "UEFA Euro"),
            ("olympicGames", "olympicGamesYear", OLY_HOSTS, "Olympics"),
        ):
            if not ev.get(evkey):
                continue
            y = year_of(ev, ykey)
            if not y:
                flag("ERROR", slug, f"{label} flag set but no year recorded")
            elif y not in hosts:
                flag("ERROR", slug, f"{label} {y} is not a known tournament year")
            elif country not in hosts[y]:
                flag("ERROR", slug,
                     f"{label} {y} was hosted by {'/'.join(hosts[y])} — stadium is in {country}")
            elif evkey == "fifaWorldCup" and y in WC_CITIES:
                d = min(km(s["lat"], s["lng"], c[0], c[1]) for c in WC_CITIES[y])
                if d > HOST_CITY_KM:
                    flag("ERROR", slug,
                         f"claims FIFA WC {y} but is {d:.0f} km from the nearest host city — not a match venue")

        # 2. no event at all
        if not ev:
            flag("REVIEW", slug, "no event recorded — classify or document why it is in the study")

        # 4. builtArea placeholder
        ba = s.get("builtArea")
        if ba is not None and not (0 < ba <= 1):
            flag("WARN", slug, f"builtArea={ba} is outside (0,1] — placeholder value, not shown in UI")

        # 5. coordinate-like name
        if re.fullmatch(r"[\d.,\s°NSEW-]+", s.get("name") or ""):
            flag("ERROR", slug, f"name {s['name']!r} looks like raw coordinates")

        # 6. scrambled city strings
        city = s.get("city") or ""
        if re.search(r"\d+°|\d+\.\d+;\s*\d+\.\d+", city):
            flag("WARN", slug, f"city field contains coordinates junk: {city[:60]!r}")
        elif country and country in city:
            flag("INFO", slug, "city field already contains the country (displays duplicated)")

        # 7. index vs stadium file consistency
        path = os.path.join(DATA, slug + ".json")
        if not os.path.exists(path):
            flag("ERROR", slug, "no data/<slug>.json file")
        else:
            m = json.load(open(path))["m"]
            if (m.get("events") or {}) != ev:
                flag("ERROR", slug, "events differ between index.json and stadium file — run scripts/sync_index.py")
            for f_idx, f_m in (("name", "name"), ("country", "country"), ("capacity", "capacity")):
                if s.get(f_idx) != m.get(f_m):
                    flag("WARN", slug, f"{f_idx} differs between index ({s.get(f_idx)!r}) and stadium file ({m.get(f_m)!r})")

    # 3. duplicates
    for i, a in enumerate(idx):
        for b in idx[i + 1:]:
            d = km(a["lat"], a["lng"], b["lat"], b["lng"])
            if d < 0.3:
                flag("REVIEW", a["slug"],
                     f"within {d*1000:.0f} m of {b['slug']} ({a['collectionDate']} vs {b['collectionDate']}) — same venue twice?")

    order = {"ERROR": 0, "WARN": 1, "REVIEW": 2, "INFO": 3}
    flags.sort(key=lambda f: (order[f[0]], f[1]))
    counts = {}
    for sev, slug, msg in flags:
        counts[sev] = counts.get(sev, 0) + 1
        print(f"{sev:6s} {slug:45s} {msg}")
    print(f"\n{len(idx)} stadiums checked — " +
          ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: order[x[0]])))
    return 1 if counts.get("ERROR") else 0


if __name__ == "__main__":
    sys.exit(main())
