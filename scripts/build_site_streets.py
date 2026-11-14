#!/usr/bin/env python3
"""Validate and publish street data used by the venue page."""

from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "cache/streets_overture"
GRAPH_DIR = ROOT / "cache/graphs_overture"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "agg/streets"
LIST_FILE = OUT_DIR / "index.json"
REPORT_CSV = ROOT / "outputs/site_streets_report.csv"
RADIUS_M = 1500
K = 111320.0
SNAP_MAX_M = 150
ORIGIN_M = 250
WALK_M_PER_MIN = 80.0
BANDS_MIN = [5, 10, 15, 20, 25]
MIN_FOR_TOP = 10
MIN_PLACES_TIED = 10
# Stored corners sit up to about 3 m off the line ends (measured on 1.41 million
# segments, largest 2.92 m). 5 m allows that; a reversed line is caught separately.
ENDPOINT_TOL_M = 5.0

os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "cache/mpl"))

import argparse
import csv
import json
import math

import numpy as np
import osmnx as ox
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


OUTPUT_KEYS = [
    "slug", "lat", "lng", "n_nodes", "origin_fallback", "segs", "iso",
    "node_kinds", "band_stats", "places",
]
REPORT_FIELDS = [
    "slug", "status", "n_segs", "places_total", "places_tied", "places_off",
    "b5", "b10", "b15", "b20", "b25", "bytes",
]


def fail(slug, check, actual, expected):
    raise SystemExit(f"{slug}: {check}: {actual!r} != {expected!r}")


def check(slug, name, actual, expected):
    if actual != expected:
        fail(slug, name, actual, expected)


def local_xy(lat, lng, lat0, lng0):
    return (
        (lng - lng0) * K * math.cos(math.radians(lat0)),
        (lat - lat0) * K,
    )


def top_value(rows, position):
    counts = {}
    for row in rows:
        value = row[position] or ("uncategorized" if position == 2 else "unknown")
        counts[value] = counts.get(value, 0) + 1
    return min(counts.items(), key=lambda item: (-item[1], item[0]))


def validate(slug, src, doc):
    graph = ox.load_graphml(GRAPH_DIR / f"{slug}.graphml")
    nodes = sorted(graph.nodes)
    node_index = {node: index for index, node in enumerate(nodes)}
    n_nodes = len(nodes)
    lat0 = doc["m"]["latitude"]
    lng0 = doc["m"]["longitude"]

    node_xy = np.array([
        local_xy(float(graph.nodes[node]["y"]), float(graph.nodes[node]["x"]), lat0, lng0)
        for node in nodes
    ])

    reference = {}
    for u0, v0, data in graph.edges(data=True):
        u = node_index[u0]
        v = node_index[v0]
        if u == v:
            continue
        pair = tuple(sorted((u, v)))
        length = float(data["length"])
        if pair not in reference or length < reference[pair]:
            reference[pair] = length

    segs = src["segs"]
    if not segs:
        fail(slug, "V1 shape segs", len(segs), "> 0")
    check(slug, "V1 shape n_nodes", src["n_nodes"], n_nodes)
    for index, seg in enumerate(segs):
        check(slug, f"V1 shape seg {index} items", len(seg), 8)
        for position, name in ((5, "u"), (6, "v")):
            value = seg[position]
            if type(value) is not int or not 0 <= value < n_nodes:
                fail(slug, f"V1 shape seg {index} {name}", value, f"integer in [0, {n_nodes})")

    seen = set()
    for index, seg in enumerate(segs):
        pair = tuple(sorted((seg[5], seg[6])))
        if pair in seen:
            fail(slug, f"V2 edges duplicate seg {index}", pair, "unique pair")
        seen.add(pair)
        if pair not in reference:
            fail(slug, f"V2 edges seg {index}", pair, "reference edge")
        if abs(float(seg[7]) - reference[pair]) > 0.051:
            fail(slug, f"V2 edges length seg {index}", seg[7], reference[pair])
    check(slug, "V2 edges set", seen, set(reference))

    for index, seg in enumerate(segs):
        first_lng, first_lat = seg[0][0]
        last_lng, last_lat = seg[0][-1]
        first = np.array(local_xy(first_lat, first_lng, lat0, lng0))
        last = np.array(local_xy(last_lat, last_lng, lat0, lng0))
        first_distance = float(np.linalg.norm(first - node_xy[seg[5]]))
        last_distance = float(np.linalg.norm(last - node_xy[seg[6]]))
        if first_distance > ENDPOINT_TOL_M:
            fail(slug, f"V3 direction first seg {index}", first_distance, f"<= {ENDPOINT_TOL_M}")
        if last_distance > ENDPOINT_TOL_M:
            fail(slug, f"V3 direction last seg {index}", last_distance, f"<= {ENDPOINT_TOL_M}")
        # Direction itself: the line must not fit its two corners better the other way round.
        swapped = float(np.linalg.norm(first - node_xy[seg[6]]) + np.linalg.norm(last - node_xy[seg[5]]))
        if swapped < first_distance + last_distance - 0.5:
            fail(slug, f"V3 direction reversed seg {index}", swapped, first_distance + last_distance)

    origins = np.flatnonzero(np.hypot(node_xy[:, 0], node_xy[:, 1]) <= ORIGIN_M).tolist()
    fallback = not origins
    if fallback:
        origins = [int(np.argmin(np.hypot(node_xy[:, 0], node_xy[:, 1])))]
    check(slug, "V4 origins fallback", src["origin_fallback"], fallback)

    rows = []
    cols = []
    weights = []
    for (u, v), length in reference.items():
        rows.extend((u, v))
        cols.extend((v, u))
        weights.extend((length, length))
    matrix = csr_matrix((weights, (rows, cols)), shape=(n_nodes, n_nodes))
    distances = dijkstra(matrix, directed=False, indices=origins, min_only=True)

    expected_bands = []
    for index, seg in enumerate(segs):
        maximum = max(distances[seg[5]], distances[seg[6]])
        expected = next((band for band in BANDS_MIN if maximum <= band * WALK_M_PER_MIN), None)
        expected_bands.append(expected)
        check(slug, f"V6 bands seg {index}", seg[3], expected)

    inside = []
    tied = []
    for row in doc["p"]:
        x, y = local_xy(row[0], row[1], lat0, lng0)
        if math.hypot(x, y) > RADIUS_M:
            continue
        inside.append(row)
        node_distances = np.hypot(node_xy[:, 0] - x, node_xy[:, 1] - y)
        node = int(np.argmin(node_distances))
        if node_distances[node] <= SNAP_MAX_M:
            tied.append((row, node))
    expected_places = {
        "total": len(inside),
        "tied": len(tied),
        "off": len(inside) - len(tied),
    }
    for band in BANDS_MIN:
        expected_places[f"b{band}"] = sum(
            distances[node] <= band * WALK_M_PER_MIN for _, node in tied
        )
    for key, value in expected_places.items():
        check(slug, f"V7 places {key}", src["places"].get(key), value)

    expected_node_kinds = {}
    for row, node in tied:
        kind = row[2] or "uncategorized"
        counts = expected_node_kinds.setdefault(str(node), {})
        counts[kind] = counts.get(kind, 0) + 1
    for node, counts in src["node_kinds"].items():
        if not isinstance(node, str) or not isinstance(counts, dict):
            fail(slug, "V8 node_kinds shape", {node: counts}, "string key and dict value")
        for kind, value in counts.items():
            if not isinstance(kind, str) or type(value) is not int or value <= 0:
                fail(slug, "V8 node_kinds value", {kind: value}, "string to positive integer")
    check(slug, "V8 node_kinds", src["node_kinds"], expected_node_kinds)

    check(slug, "V9 band_stats length", len(src["band_stats"]), len(BANDS_MIN))
    for position, upper in enumerate(BANDS_MIN):
        lower = 0 if position == 0 else BANDS_MIN[position - 1]
        ring_rows = [
            row for row, node in tied
            if ((distances[node] >= 0 if lower == 0 else distances[node] > lower * WALK_M_PER_MIN)
                and distances[node] <= upper * WALK_M_PER_MIN)
        ]
        item = src["band_stats"][position]
        for key, expected in (("from", lower), ("to", upper), ("places", len(ring_rows))):
            check(slug, f"V9 band_stats {upper} {key}", item.get(key), expected)
        street_km = sum(
            reference[tuple(sorted((seg[5], seg[6])))]
            for seg, band in zip(segs, expected_bands)
            if band == upper and seg[2] is not None
        ) / 1000
        if abs(street_km - item["street_km"]) > 0.011:
            fail(slug, f"V9 band_stats {upper} street_km", item["street_km"], street_km)
        per_km = round(len(ring_rows) / item["street_km"], 1) if item["street_km"] else None
        check(slug, f"V9 band_stats {upper} per_km", item["per_km"], per_km)
        expected_top = {"top_kind": None, "top_kind_n": None, "top_type": None, "top_type_n": None}
        if len(ring_rows) >= MIN_FOR_TOP:
            kind, kind_n = top_value(ring_rows, 2)
            place_type, type_n = top_value(ring_rows, 4)
            expected_top = {
                "top_kind": kind, "top_kind_n": kind_n,
                "top_type": place_type, "top_type_n": type_n,
            }
        for key, expected in expected_top.items():
            check(slug, f"V9 band_stats {upper} {key}", item.get(key), expected)

    check(slug, "V10 iso keys", set(src["iso"]), {str(band) for band in BANDS_MIN})
    for band in BANDS_MIN:
        if not isinstance(src["iso"][str(band)], list):
            fail(slug, f"V10 iso {band}", type(src["iso"][str(band)]).__name__, "list")


def report_row(slug, status, src=None, byte_count=""):
    places = src["places"] if src else {}
    return {
        "slug": slug,
        "status": status,
        "n_segs": len(src["segs"]) if src else "",
        "places_total": places.get("total", ""),
        "places_tied": places.get("tied", ""),
        "places_off": places.get("off", ""),
        **{f"b{band}": places.get(f"b{band}", "") for band in BANDS_MIN},
        "bytes": byte_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all == bool(args.slugs):
        parser.error("use --all or provide one or more slugs")
    if args.all:
        with (DATA_DIR / "index.json").open() as handle:
            slugs = [venue["slug"] for venue in json.load(handle)]
    else:
        slugs = args.slugs

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    counts = {}
    bytes_written = 0
    for slug in slugs:
        source_path = SRC_DIR / f"{slug}.json"
        if not source_path.exists():
            status = "no_source"
            rows.append(report_row(slug, status))
            counts[status] = counts.get(status, 0) + 1
            continue
        with source_path.open() as handle:
            src = json.load(handle)
        with (DATA_DIR / f"{slug}.json").open() as handle:
            doc = json.load(handle)
        validate(slug, src, doc)
        output_path = OUT_DIR / f"{slug}.json"
        if src["places"]["tied"] < MIN_PLACES_TIED:
            status = "too_few_places"
            output_path.unlink(missing_ok=True)
            rows.append(report_row(slug, status, src))
        else:
            status = "written"
            output = {key: src[key] for key in OUTPUT_KEYS}
            with output_path.open("w") as handle:
                json.dump(output, handle, ensure_ascii=False, separators=(",", ":"))
            byte_count = output_path.stat().st_size
            bytes_written += byte_count
            rows.append(report_row(slug, status, src, byte_count))
        counts[status] = counts.get(status, 0) + 1

    venue_slugs = sorted(path.stem for path in OUT_DIR.glob("*.json") if path != LIST_FILE)
    with LIST_FILE.open("w") as handle:
        json.dump({"min_places": MIN_PLACES_TIED, "venues": venue_slugs}, handle,
                  ensure_ascii=False, separators=(",", ":"))
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    # A run on a few slugs must not erase the other venues' rows: keep them, replace ours.
    if REPORT_CSV.exists():
        done = {row["slug"] for row in rows}
        with REPORT_CSV.open(newline="") as handle:
            rows = [r for r in csv.DictReader(handle) if r["slug"] not in done] + rows
        rows.sort(key=lambda r: r["slug"])
    with REPORT_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    for status in sorted(counts):
        print(f"{status}: {counts[status]}")
    print(f"total bytes written: {bytes_written}")


if __name__ == "__main__":
    main()
