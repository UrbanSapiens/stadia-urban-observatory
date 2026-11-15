#!/usr/bin/env python3
"""Refresh place-derived street measures from the core venue database."""
import argparse
import csv
import json
import math
from pathlib import Path

import networkx as nx

from build_street_measures import (
    BANDS_MIN, MIN_FOR_TOP, ORIGIN_M, RADIUS_M, WALK_M_PER_MIN,
    edge_band, snap, street_graph, to_xy,
)

# ---------------- config ----------------
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CORE = ROOT / "core/venues"
GRAPH_DIR = ROOT / "cache/graphs_overture"
SOURCE_DIR = ROOT / "cache/streets_overture"
OUT_DIR = ROOT / "cache/streets_core"
# ----------------------------------------


def core_places(slug, lat0, lng0):
    path = CORE / slug / "places.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing core places file: {slug}: {path}")
    places = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["keep"] not in {"1", "True", "true"}:
                continue
            x, y = to_xy(float(row["lat"]), float(row["lon"]), lat0, lng0)
            if math.hypot(x, y) <= RADIUS_M:
                places.append((x, y, row["category"] or "uncategorized", row["type"] or "unknown"))
    return places


def process(slug):
    output_path = OUT_DIR / f"{slug}.json"
    if output_path.exists():
        print(f"{slug}: already done", flush=True)
        return
    source_path = SOURCE_DIR / f"{slug}.json"
    if not source_path.exists():
        raise FileNotFoundError(f"missing street source: {slug}: {source_path}")
    src = json.loads(source_path.read_text(encoding="utf-8"))
    doc = json.loads((DATA / f"{slug}.json").read_text(encoding="utf-8"))
    lat0 = float(doc["m"]["latitude"])
    lng0 = float(doc["m"]["longitude"])
    graph = street_graph(GRAPH_DIR / f"{slug}.graphml")

    nodes = list(graph.nodes)
    node_xy = [to_xy(graph.nodes[node]["lat"], graph.nodes[node]["lng"], lat0, lng0) for node in nodes]
    origins = [node for node, (x, y) in zip(nodes, node_xy) if math.hypot(x, y) <= ORIGIN_M]
    if not origins:
        origins = [min(zip(nodes, node_xy), key=lambda item: math.hypot(*item[1]))[0]]
    dist = nx.multi_source_dijkstra_path_length(graph, origins, weight="length")
    edges = list(graph.edges(data=True))
    bands = {(u, v): edge_band(u, v, dist) for u, v, _ in edges}

    places = core_places(slug, lat0, lng0)
    tied, off, _ = snap(graph, places, lat0, lng0)
    place_counts = {band: sum(node in dist and dist[node] <= band * WALK_M_PER_MIN
                              for node, _, _ in tied) for band in BANDS_MIN}
    node_indexes = {node: index for index, node in enumerate(sorted(graph.nodes))}
    node_kinds = {}
    for node, kind, _ in tied:
        key = str(node_indexes[node])
        counts = node_kinds.setdefault(key, {})
        counts[kind] = counts.get(kind, 0) + 1

    band_stats = []
    lower = 0
    for position, band in enumerate(BANDS_MIN):
        ring_places = [(kind, place_type) for node, kind, place_type in tied
                       if node in dist and (dist[node] >= 0 if lower == 0 else dist[node] > lower * WALK_M_PER_MIN)
                       and dist[node] <= band * WALK_M_PER_MIN]
        kind_counts = {}
        type_counts = {}
        for kind, place_type in ring_places:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            type_counts[place_type] = type_counts.get(place_type, 0) + 1
        top_kind = min(kind_counts, key=lambda value: (-kind_counts[value], value)) if kind_counts else None
        top_type = min(type_counts, key=lambda value: (-type_counts[value], value)) if type_counts else None
        enough = len(ring_places) >= MIN_FOR_TOP
        street_km = src["band_stats"][position]["street_km"]
        calculated = round(sum(data["length"] for position, (u, v, data) in enumerate(edges)
                               if bands[(u, v)] == band and src["segs"][position][2] is not None) / 1000, 2)
        assert abs(street_km - calculated) <= 0.011, (slug, band, street_km, calculated)
        band_stats.append({
            "from": lower, "to": band, "places": len(ring_places), "street_km": street_km,
            "per_km": round(len(ring_places) / street_km, 1) if street_km else None,
            "top_kind": top_kind if enough else None,
            "top_kind_n": kind_counts[top_kind] if enough else None,
            "top_type": top_type if enough else None,
            "top_type_n": type_counts[top_type] if enough else None,
        })
        lower = band

    src["node_kinds"] = node_kinds
    src["band_stats"] = band_stats
    src["places"] = {"total": len(places), "tied": len(tied), "off": off,
                     **{f"b{band}": place_counts[band] for band in BANDS_MIN}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(src, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{slug}: written", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all == bool(args.slugs):
        parser.error("use --all or provide one or more slugs")
    if args.all:
        slugs = [venue["slug"] for venue in json.loads((DATA / "index.json").read_text())]
    else:
        slugs = args.slugs
    for slug in slugs:
        process(slug)


if __name__ == "__main__":
    main()
