#!/usr/bin/env python3
"""Build the aggregate files the public site reads. No place coordinates leave this script.

Run from the repo root, after audit_data.py and sync_index.py:
    python3 scripts/build_site_data.py
Out:  agg/index.json        venue list, study distributions, atlas plates
      agg/<slug>.json       one venue: counts per grid cell and category, counts per
                            distance ring, top types, summary numbers, lede sentences

Input is data/<slug>.json (the per-place records). Those stay the working data;
the site itself only fetches agg/.

Every lede sentence is generated here and re-derived from the raw records by
validate(); the build stops if one is not true.

Reserved for later: PAPER_MEASURES. The measures of the unpublished paper are
not computed or published here. When the paper is accepted, add them to
aggregate() under the "paper" key and switch SHOW_PAPER_MEASURES on in index.html.
"""
import csv, json, math, statistics
from pathlib import Path

# ---------------- config ----------------
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "agg"
ARCHITECTS = ROOT / "outputs" / "architects_site.csv"   # scripts/build_architects_site.py (value + its source)
BUILT = ROOT / "outputs" / "built_proportion_all.csv"     # scripts/compare_building_sources.py --all
GRID_N = 70          # cells per side over the 3 km square (grid of the printed models)
RADIUS_M = 1500
RING_M = 150         # straight-line ring width
N_TYPES = 10         # most common types kept per venue, overall
N_TYPES_PER_KIND = 8 # and within each kind, so the type list can follow the selected kind
N_RING_TYPES = 3     # most common types listed per distance ring
VAGUE_TYPES = {"unknown", "point of interest", "establishment", "building"}   # platform labels that name no activity
MIX_MIN_PLACES = 5   # a cell needs this many places before its mix of kinds is computed
MIX_MIN_CELLS = 10   # a venue needs this many such cells before it gets a venue-level mix
N_ATLAS = 24         # venues drawn on the landing atlas, at even steps by place count
YEAR_MIN, YEAR_MAX = 1890, 2026   # opening years outside this range are treated as unreliable
K = 111320.0         # metres per degree of latitude
PROGRAMME = {"FIFA World Cup": "wc", "Olympic Games": "og", "UEFA Euro Cup": "eu",
             "MLS": "mls", "MLB": "mlb", "China Stadium Diplomacy": "csd", "": "none"}
EVENT_TAGS = [("fifaWorldCup", "FIFA World Cup", "fifaWorldCupYear"), ("olympicGames", "Olympic Games", "olympicGamesYear"),
              ("uefaEuroCup", "UEFA Euro", "uefaEuroCupYear"), ("mls", "MLS", None), ("mlb", "MLB", None),
              ("nfl", "NFL", None), ("nwsl", "NWSL", None), ("chinaStadiumDiplomacy", "China stadium diplomacy", None)]
# ----------------------------------------


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def reliable_year(y):
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None
    return y if YEAR_MIN <= y <= YEAR_MAX else None


def records(doc):
    """Records inside the circle as (dx, dy, straight-line m, category, type, network m)."""
    m = doc["m"]
    lat0, lng0 = m["latitude"], m["longitude"]
    kx = K * math.cos(math.radians(lat0))
    out = []
    for p in doc["p"]:
        dx, dy = (p[1] - lng0) * kx, (p[0] - lat0) * K
        d = math.hypot(dx, dy)
        if d <= RADIUS_M:
            out.append((dx, dy, d, (p[2] or "uncategorized"), (p[4] or "unknown"), p[3]))
    return out


def simpson(counts):
    n = sum(counts)
    return 1 - sum(x * (x - 1) for x in counts) / (n * (n - 1)) if n > 1 else 0.0


def cell_of(dx, dy):
    cell_m = 2 * RADIUS_M / GRID_N
    col = min(GRID_N - 1, max(0, int((dx + RADIUS_M) / cell_m)))
    row = min(GRID_N - 1, max(0, int((RADIUS_M - dy) / cell_m)))
    return row * GRID_N + col


def flat(d):
    return [x for kv in sorted(d.items()) for x in kv]


def ring_types(counts):
    """Most common types of one ring as [type, count], ties broken by name. Vague platform labels are left out."""
    named = [(t, c) for t, c in counts.items() if t not in VAGUE_TYPES]
    return [[t, c] for t, c in sorted(named, key=lambda tc: (-tc[1], tc[0]))[:N_RING_TYPES]]


def aggregate(entry, doc, recs, rank, n_all, architect, built):
    m = doc["m"]
    n = len(recs)
    cats, types = {}, {}
    for r in recs:
        cats[r[3]] = cats.get(r[3], 0) + 1
        types[(r[4], r[3])] = types.get((r[4], r[3]), 0) + 1
    cat_order = [c for c, _ in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))]
    cidx = {c: i for i, c in enumerate(cat_order)}
    ranked_types = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
    # Types the page can list: the overall top, plus the top of each kind.
    keep = [k for k, _ in ranked_types[:N_TYPES]]
    by_kind = {c: [k for k, _ in ranked_types if k[1] == c][:N_TYPES_PER_KIND] for c in cat_order}
    for c in cat_order:
        keep += [k for k in by_kind[c] if k not in keep]
    top_types = [(k, types[k]) for k in keep]
    tidx = {k: i for i, (k, _) in enumerate(top_types)}
    kind_n_types = [sum(1 for k in types if k[1] == c) for c in cat_order]
    grid = [dict() for _ in cat_order]
    tgrid = [dict() for _ in top_types]
    for dx, dy, _, c, t, _ in recs:
        k = cell_of(dx, dy)
        g = grid[cidx[c]]
        g[k] = g.get(k, 0) + 1
        if (t, c) in tidx:
            tg = tgrid[tidx[(t, c)]]
            tg[k] = tg.get(k, 0) + 1
    n_rings = RADIUS_M // RING_M
    rings = [0] * n_rings
    ring_kinds = [dict() for _ in range(n_rings)]     # what is in each ring: count per kind
    ring_type_counts = [dict() for _ in range(n_rings)]
    for r in recs:
        j = min(n_rings - 1, int(r[2] // RING_M))
        rings[j] += 1
        ring_kinds[j][cidx[r[3]]] = ring_kinds[j].get(cidx[r[3]], 0) + 1
        ring_type_counts[j][r[4]] = ring_type_counts[j].get(r[4], 0) + 1
    ring_density = [round(c / (math.pi * (((i + 1) * RING_M) ** 2 - (i * RING_M) ** 2) / 1e6), 1) for i, c in enumerate(rings)]
    # Mix of kinds inside a cell: Simpson index, 1 - sum n(n-1) / N(N-1). Quantity is not in it.
    per_cell = {}
    for ci, g in enumerate(grid):
        for k, c in g.items():
            per_cell.setdefault(k, [0] * len(cat_order))[ci] += c
    mixes = [(simpson(c), sum(c)) for c in per_cell.values() if sum(c) >= MIX_MIN_PLACES]
    cell_mix = round(sum(v * w for v, w in mixes) / sum(w for _, w in mixes), 3) if len(mixes) >= MIX_MIN_CELLS else None
    lin = [r[2] for r in recs]
    net = [r[5] for r in recs if r[5] is not None]
    med_lin = round(statistics.median(lin)) if lin else None
    med_net = round(statistics.median(net)) if net else None
    s = m.get("stats") or {}
    ev = m.get("events") or {}
    tags = [label + (f" {str(ev.get(yk, '')).replace('.0', '')}" if yk and ev.get(yk) else "")
            for key, label, yk in EVENT_TAGS if ev.get(key)]

    # ---- lede: short factual sentences, each one checked in validate() ----
    lede = []
    if n:
        lede.append(f"{n:,} mapped place{'s' if n != 1 else ''} within 1.5 km. {ordinal(rank)} of {n_all} venues by count.")
    if n >= 20:
        top = cat_order[0]
        share = round(100 * cats[top] / n)
        top3 = round(100 * sum(cats[c] for c in cat_order[:3]) / n)
        lede.append(f"{top.capitalize()} leads with {share}% of places. The top three categories hold {top3}%.")
        best = max(range(n_rings), key=lambda i: ring_density[i])
        lede.append(f"Places are densest {best * RING_M} to {(best + 1) * RING_M} m from the stadium: {round(ring_density[best]):,} per km².")
        if med_net and med_lin:
            lede.append(f"Half of the places are within {med_lin:,} m in a straight line, {med_net:,} m by street.")
    if 0 < n < 200:
        lede.append("A count this low can reflect thin map coverage, not an empty area.")

    return {"slug": entry["slug"], "name": m["name"], "country": m.get("country") or "", "tags": tags,
            "lat": m["latitude"], "lng": m["longitude"],            # the stadium itself, not a place record
            "architect": architect[0], "architectSource": architect[1], "collected": m.get("collectionLabel"),
            "opened": reliable_year(m.get("openingYear")), "openedKind": m.get("openingYearKind") or "opened",
            "capacity": entry.get("capacity"),
            "n": n, "rank": rank, "cats": [[c, cats[c]] for c in cat_order],
            "grid": [flat(g) for g in grid],
            # types: every type the page can list. topTypes / kindTypes index into it.
            "types": [[t, c, v] for (t, c), v in top_types], "typeGrid": [flat(g) for g in tgrid],
            "topTypes": list(range(min(N_TYPES, len(top_types)))),
            "kindTypes": [[tidx[k] for k in by_kind[c]] for c in cat_order], "kindNTypes": kind_n_types,
            "cellMix": cell_mix, "cellMixCells": len(mixes),
            "rings": rings, "ringDensity": ring_density,
            "ringKinds": [flat(g) for g in ring_kinds], "ringTypes": [ring_types(t) for t in ring_type_counts],
            "built": built, "medLin": med_lin, "medNet": med_net,
            "net": {"intersections": s.get("numberOfIntersections"),
                    "block": round(s["avgEdgeLength"], 1) if s.get("avgEdgeLength") else None,
                    "streetKm": round(s["edgeLengthTotal"] / 1000, 1) if s.get("edgeLengthTotal") else None},
            "lede": lede}


def validate(agg, doc):
    """Re-derive every number used in the lede from the raw records, by a different route."""
    m = doc["m"]
    kx = K * math.cos(math.radians(m["latitude"]))
    inside = []
    for p in doc["p"]:
        d = math.sqrt(((p[1] - m["longitude"]) * kx) ** 2 + ((p[0] - m["latitude"]) * K) ** 2)
        if d <= RADIUS_M:
            inside.append((d, p[2] or "uncategorized", p[4] or "unknown"))
    slug = agg["slug"]
    assert len(inside) == agg["n"], (slug, "count")
    assert sum(c for _, c in agg["cats"]) == agg["n"], (slug, "category total")
    assert sum(sum(g[1::2]) for g in agg["grid"]) == agg["n"], (slug, "grid total")
    assert sum(agg["rings"]) == agg["n"], (slug, "ring total")
    # Ring panel: kinds and most common types per ring, recounted ring by ring from the records.
    names = [c for c, _ in agg["cats"]]
    for j, (pairs, listed) in enumerate(zip(agg["ringKinds"], agg["ringTypes"])):
        here = [x for x in inside if min(len(agg["rings"]) - 1, int(x[0] // RING_M)) == j]
        assert len(here) == agg["rings"][j] == sum(pairs[1::2]), (slug, "ring kinds total", j)
        for ci, c in zip(pairs[0::2], pairs[1::2]):
            assert sum(1 for x in here if x[1] == names[ci]) == c, (slug, "ring kind", j, names[ci])
        tally = {}
        for x in here:
            tally[x[2]] = tally.get(x[2], 0) + 1
        for t, c in listed:
            assert t not in VAGUE_TYPES and tally[t] == c, (slug, "ring type", j, t)
        floor = listed[-1][1] if len(listed) == N_RING_TYPES else 0
        assert all(c <= floor for t, c in tally.items() if t not in VAGUE_TYPES and t not in [l[0] for l in listed]), (slug, "ring type order", j)
    if agg["built"]:
        assert 0 < agg["built"]["share"] <= 100, (slug, "built share")
    for (t, c, v), g in zip(agg["types"], agg["typeGrid"]):
        assert sum(g[1::2]) == v, (slug, "type grid", t)
    for ci, idxs in enumerate(agg["kindTypes"]):
        assert all(agg["types"][i][1] == agg["cats"][ci][0] for i in idxs), (slug, "kind types")
        counts = [agg["types"][i][2] for i in idxs]
        assert counts == sorted(counts, reverse=True), (slug, "kind types order")
    if agg["n"] >= 20:
        top = agg["cats"][0][0]
        assert sum(1 for x in inside if x[1] == top) == agg["cats"][0][1], (slug, "top category")
        assert all(agg["cats"][0][1] >= c for _, c in agg["cats"]), (slug, "top category is the largest")
        ds = sorted(x[0] for x in inside)
        h = len(ds) // 2
        mid = ds[h] if len(ds) % 2 else (ds[h - 1] + ds[h]) / 2      # median by position, not statistics.median
        assert abs(mid - agg["medLin"]) <= 1, (slug, "median straight-line", mid, agg["medLin"])
        best = max(range(len(agg["ringDensity"])), key=lambda i: agg["ringDensity"][i])
        last = len(agg["rings"]) - 1
        cnt = sum(1 for x in inside if min(last, int(x[0] // RING_M)) == best)
        assert cnt == agg["rings"][best], (slug, "densest ring count")


def main():
    ix = json.load(open(DATA / "index.json"))
    arch = {}
    if ARCHITECTS.exists():
        arch = {r["slug"]: (r["architects"], r["source"]) for r in csv.DictReader(open(ARCHITECTS, encoding="utf-8")) if r["architects"]}
    built = {}
    if BUILT.exists():
        for r in csv.DictReader(open(BUILT, encoding="utf-8")):
            if r["status"] == "ok" and 0 < float(r["ratio_all"]) <= 1:
                # share of the 1,500 m circle covered by building footprints, in percent
                built[r["slug"]] = {"share": round(100 * float(r["ratio_all"]), 1),
                                    "osmOnly": all(int(r[k]) == 0 for k in ("n_google", "n_microsoft", "n_other"))}
    docs = {x["slug"]: json.load(open(DATA / f"{x['slug']}.json")) for x in ix}
    recs = {s: records(d) for s, d in docs.items()}
    by_count = sorted(ix, key=lambda x: (-len(recs[x["slug"]]), x["slug"]))
    rank = {x["slug"]: i + 1 for i, x in enumerate(by_count)}
    assert rank[by_count[0]["slug"]] == 1 and len(set(rank.values())) == len(ix)

    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    aggs, n_lede = {}, 0
    for x in ix:
        s = x["slug"]
        a = aggregate(x, docs[s], recs[s], rank[s], len(ix), arch.get(s, (None, None)), built.get(s))
        validate(a, docs[s])
        assert not any(k in a for k in ("p", "places", "points")), "raw records must not be published"
        a["study"] = {"places": a["n"], "cellMix": a["cellMix"], "medianDist": x.get("medianDist"),
                      "built": a["built"]["share"] if a["built"] else None,
                      "intersections": x.get("intersections"), "blockLength": x.get("blockLength")}
        aggs[s] = a
        n_lede += len(a["lede"])
    # Rank of each venue in each distance ring (1 = most places per km2 in that ring).
    n_rings = RADIUS_M // RING_M
    for i in range(n_rings):
        order = sorted(aggs, key=lambda s: (-aggs[s]["ringDensity"][i], s))
        for pos, s in enumerate(order):
            aggs[s].setdefault("ringRank", [0] * n_rings)[i] = pos + 1
    for s, a in aggs.items():
        best = min(range(n_rings), key=lambda i: a["ringRank"][i])
        assert sum(1 for t in aggs.values() if t["ringDensity"][best] > a["ringDensity"][best]) < a["ringRank"][best], (s, "ring rank")
        (OUT / f"{s}.json").write_text(json.dumps(a, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ordered = sorted(ix, key=lambda x: (len(recs[x["slug"]]), x["slug"]))
    pick = sorted({round(i * (len(ordered) - 1) / (N_ATLAS - 1)) for i in range(N_ATLAS)})
    atlas = []
    for i in pick:
        a = aggs[ordered[i]["slug"]]
        total = {}
        for g in a["grid"]:
            for k, c in zip(g[0::2], g[1::2]):
                total[k] = total.get(k, 0) + c
        atlas.append({"slug": a["slug"], "cells": flat(total)})
    index = {
        "cfg": {"gridN": GRID_N, "radius": RADIUS_M, "ring": RING_M, "yearMin": YEAR_MIN, "yearMax": YEAR_MAX,
                "mixMinPlaces": MIX_MIN_PLACES},
        # [slug, name, country, lat, lng, openingYear|null, programme, places, rank]
        "venues": [[x["slug"], x["name"], x.get("country") or "", x["lat"], x["lng"], reliable_year(x.get("openingYear")),
                    PROGRAMME[x.get("event") or ""], aggs[x["slug"]]["n"], rank[x["slug"]]]
                   for x in sorted(ix, key=lambda x: x["name"].lower())],
        "study": {"places": sorted(a["n"] for a in aggs.values()),
                  "cellMix": sorted(a["cellMix"] for a in aggs.values() if a["cellMix"] is not None),
                  "built": sorted(a["built"]["share"] for a in aggs.values() if a["built"]),
                  "medianDist": sorted(x["medianDist"] for x in ix if x.get("medianDist")),
                  "intersections": sorted(x["intersections"] for x in ix if x.get("intersections")),
                  "blockLength": sorted(x["blockLength"] for x in ix if x.get("blockLength"))},
        "atlas": atlas,
    }
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.glob("*.json"))
    print(f"wrote agg/: {len(aggs)} venue files + index.json, {size/1e6:.1f} MB in total "
          f"(raw data/ is {sum(f.stat().st_size for f in DATA.glob('*.json'))/1e6:.0f} MB)")
    print(f"{n_lede} lede sentences validated; {sum(1 for a in aggs.values() if a['architect'])} venues with a sourced architect")


if __name__ == "__main__":
    main()
