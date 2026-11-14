#!/usr/bin/env python3
"""Build street betweenness and walking-band data for named venues."""
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
import time

import networkx as nx
import numpy as np

# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADIUS_M = 1500
K = 111320.0
SNAP_MAX_M = 150
ORIGIN_M = 250
WALK_M_PER_MIN = 80.0
BANDS_MIN = [5, 10, 15, 20, 25]
MIN_FOR_TOP = 10
HULL_OUT_M = 60
HULL_IN_M = 40
HULL_SIMPLIFY_M = 5
# Graph source is switchable so the OSMnx-based outputs stay on disk as a reference.
GRAPH_DIR = os.environ.get("STREETS_GRAPH_DIR", os.path.join(ROOT, "cache", "graphs"))
SITE_DATA = os.path.join(ROOT, "data")
OUT_DIR = os.environ.get("STREETS_OUT_DIR", os.path.join(ROOT, "cache", "streets"))
# Optional: a Python with igraph, for a much faster through-street count on large graphs.
BT_PYTHON = os.environ.get("STREETS_BT_PYTHON", "")
SUMMARY = os.environ.get("STREETS_SUMMARY", os.path.join(ROOT, "outputs", "street_measures_summary.csv"))
# ----------------------------------------

os.environ.setdefault("MPLCONFIGDIR", os.path.join(ROOT, "cache", "mpl"))
import osmnx as ox
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union


SUMMARY_COLUMNS = [
    "slug", "n_nodes", "n_edges_simple", "n_kept", "n_origin", "origin_m",
    "origin_fallback", "bt_max", "km_b5", "km_b10", "km_b15", "km_b20",
    "km_b25", "places_total", "places_tied", "places_off", "places_b5",
    "places_b10", "places_b15", "places_b20", "places_b25", "seconds",
]


def street_name(value):
    """Normalize OSM names without inventing missing text."""
    if isinstance(value, list):
        values = []
        for item in value:
            if item and item not in values:
                values.append(str(item))
        return " / ".join(values) or None
    return str(value) if value else None


def to_xy(lat, lng, lat0, lng0):
    """Local metres east and north of the stadium."""
    return (lng - lng0) * K * math.cos(math.radians(lat0)), (lat - lat0) * K


def street_graph(path):
    """Load and convert a graph to the required undirected simple graph."""
    G = ox.load_graphml(path)
    H = nx.Graph()
    for n, d in G.nodes(data=True):
        H.add_node(n, lng=float(d["x"]), lat=float(d["y"]))
    for u, v, d in G.edges(data=True):
        if u == v:
            continue
        length = float(d["length"])
        if H.has_edge(u, v) and H[u][v]["length"] <= length:
            continue
        if "geometry" in d:
            ll = [(x, y) for x, y in d["geometry"].coords]
            # Start the line at whichever end lies nearer corner u. An exact-match test
            # fails when the stored corner sits a few centimetres off the line's end.
            ux, uy = H.nodes[u]["lng"], H.nodes[u]["lat"]
            if (abs(ll[0][0] - ux) + abs(ll[0][1] - uy)
                    > abs(ll[-1][0] - ux) + abs(ll[-1][1] - uy)):
                ll.reverse()
        else:
            ll = [(H.nodes[u]["lng"], H.nodes[u]["lat"]), (H.nodes[v]["lng"], H.nodes[v]["lat"])]
        H.add_edge(u, v, length=length, ll=ll, first=u, name=street_name(d.get("name")))
    return H


def edge_betweenness(H):
    """Shortest-route count per segment. Uses the igraph helper when BT_PYTHON is set."""
    if not BT_PYTHON:
        return nx.edge_betweenness_centrality(H, weight="length", normalized=False)
    index = {node: i for i, node in enumerate(H.nodes)}
    edges = [(u, v, d["length"]) for u, v, d in H.edges(data=True)]
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump({"n": len(index), "edges": [[index[u], index[v], length] for u, v, length in edges]}, f)
        subprocess.run([BT_PYTHON, os.path.join(ROOT, "scripts", "edge_betweenness_fast.py"), src, dst], check=True)
        values = json.load(open(dst, encoding="utf-8"))
    return {(u, v): value for (u, v, _), value in zip(edges, values)}


def site_places(slug, lat0, lng0):
    """Return local coordinates, kinds and types inside the circle."""
    doc = json.load(open(os.path.join(SITE_DATA, slug + ".json"), encoding="utf-8"))
    out = []
    for p in doc["p"]:
        x, y = to_xy(p[0], p[1], lat0, lng0)
        if math.hypot(x, y) <= RADIUS_M:
            out.append((x, y, p[2] or "uncategorized", p[4] or "unknown"))
    return out


def snap(H, places, lat0, lng0):
    nodes = list(H.nodes)
    tree = cKDTree([to_xy(H.nodes[n]["lat"], H.nodes[n]["lng"], lat0, lng0) for n in nodes])
    dist, idx = tree.query([(x, y) for x, y, _, _ in places])
    tied = [(nodes[i], k, t) for (x, y, k, t), d, i in zip(places, dist, idx)
            if d <= SNAP_MAX_M]
    return tied, len(places) - len(tied), float(np.median(dist)) if len(dist) else 0.0


def edge_midpoint_xy(d, lat0, lng0):
    xy = [to_xy(lat, lng, lat0, lng0) for lng, lat in d["ll"]]
    if len(xy) == 2:
        return ((xy[0][0] + xy[1][0]) / 2, (xy[0][1] + xy[1][1]) / 2)
    return xy[len(xy) // 2]


def edge_band(u, v, dist):
    if u not in dist or v not in dist:
        return None
    farther = max(dist[u], dist[v])
    for band in BANDS_MIN:
        if farther <= band * WALK_M_PER_MIN:
            return band
    return None


def stored_midpoint_distance(d, lat0, lng0):
    """Measure the midpoint after required coordinate rounding."""
    ll = [(round(lng, 6), round(lat, 6)) for lng, lat in d["ll"]]
    xy = [to_xy(lat, lng, lat0, lng0) for lng, lat in ll]
    point = xy[len(xy) // 2] if len(xy) > 2 else ((xy[0][0] + xy[-1][0]) / 2,
                                                   (xy[0][1] + xy[-1][1]) / 2)
    return math.hypot(*point)


def polygon_rings(poly, lat0, lng0):
    scale_x = K * math.cos(math.radians(lat0))

    def ring(coords):
        return [[round(lng0 + x / scale_x, 6), round(lat0 + y / K, 6)] for x, y in coords]

    return [ring(poly.exterior.coords)] + [ring(r.coords) for r in poly.interiors]


def band_polygons(H, bands, limit, lat0, lng0):
    lines = []
    for u, v, d in H.edges(data=True):
        if bands[(u, v)] is not None and bands[(u, v)] <= limit:
            lines.append(LineString([to_xy(lat, lng, lat0, lng0) for lng, lat in d["ll"]]))
    if not lines:
        return []
    shape = unary_union(lines).buffer(HULL_OUT_M).buffer(-HULL_IN_M).simplify(HULL_SIMPLIFY_M)
    if shape.is_empty:
        return []
    polygons = [shape] if isinstance(shape, Polygon) else list(shape.geoms) if isinstance(shape, MultiPolygon) else []
    return [polygon_rings(poly, lat0, lng0) for poly in polygons]


def existing_summary():
    if not os.path.exists(SUMMARY):
        return {}
    with open(SUMMARY, newline="", encoding="utf-8") as f:
        return {row["slug"]: {column: row.get(column, "") for column in SUMMARY_COLUMNS}
                for row in csv.DictReader(f)}


def write_summary(rows):
    with open(SUMMARY, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows.values())


def process(slug):
    started = time.monotonic()
    doc = json.load(open(os.path.join(SITE_DATA, slug + ".json"), encoding="utf-8"))
    m = doc["m"]
    lat0, lng0 = float(m["latitude"]), float(m["longitude"])
    H = street_graph(os.path.join(GRAPH_DIR, slug + ".graphml"))

    bt = edge_betweenness(H)
    kept = [(u, v, d) for u, v, d in H.edges(data=True)
            if math.hypot(*edge_midpoint_xy(d, lat0, lng0)) <= RADIUS_M
            and stored_midpoint_distance(d, lat0, lng0) <= RADIUS_M]
    kept_values = [bt[(u, v)] for u, v, _ in kept]
    n_kept = len(kept)

    nodes = list(H.nodes)
    tree = cKDTree([to_xy(H.nodes[n]["lat"], H.nodes[n]["lng"], lat0, lng0) for n in nodes])
    node_distances = np.asarray(tree.query([(0.0, 0.0)], k=len(nodes))[0]).reshape(-1)
    node_indices = np.asarray(tree.query([(0.0, 0.0)], k=len(nodes))[1]).reshape(-1)
    origins = [nodes[i] for d, i in zip(node_distances, node_indices) if d <= ORIGIN_M]
    origin_fallback = not origins
    if origin_fallback:
        origins = [nodes[int(node_indices[0])]]
        origin_m = round(float(node_distances[0]))
    else:
        origin_m = ORIGIN_M
    dist = nx.multi_source_dijkstra_path_length(H, origins, weight="length")
    bands = {(u, v): edge_band(u, v, dist) for u, v in H.edges}

    kept_pr = {}
    for (u, v, _), value in zip(kept, kept_values):
        kept_pr[frozenset((u, v))] = round(100 * sum(other <= value for other in kept_values) / n_kept)
    sorted_nodes = sorted(H.nodes)
    node_indexes = {node: index for index, node in enumerate(sorted_nodes)}
    segs = []
    for u, v, d in H.edges(data=True):
        pr = kept_pr.get(frozenset((u, v)))
        # The line must run from corner u to corner v: the browser measures a click's
        # position along the line and assigns it to u and v in that order.
        oriented = d["ll"] if d["first"] == u else d["ll"][::-1]
        ll = [[round(lng, 6), round(lat, 6)] for lng, lat in oriented]
        segs.append([ll, round(bt[(u, v)], 1), pr, bands[(u, v)], d["name"],
                     node_indexes[u], node_indexes[v], round(d["length"], 1)])

    places = site_places(slug, lat0, lng0)
    tied, off, _ = snap(H, places, lat0, lng0)
    place_counts = {band: sum(node in dist and dist[node] <= band * WALK_M_PER_MIN
                              for node, _, _ in tied)
                    for band in BANDS_MIN}
    node_kinds = {}
    for node, kind, _ in tied:
        key = str(node_indexes[node])
        node_kinds.setdefault(key, {})[kind] = node_kinds.setdefault(key, {}).get(kind, 0) + 1

    band_stats = []
    lower = 0
    for band in BANDS_MIN:
        ring_places = [(kind, place_type) for node, kind, place_type in tied
                       if node in dist and (dist[node] >= 0 if lower == 0 else dist[node] > lower * WALK_M_PER_MIN)
                       and dist[node] <= band * WALK_M_PER_MIN]
        street_km = round(sum(d["length"] for u, v, d in kept
                              if bands[(u, v)] == band) / 1000, 2)
        kind_counts = {}
        type_counts = {}
        for kind, place_type in ring_places:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            type_counts[place_type] = type_counts.get(place_type, 0) + 1
        top_kind = min(kind_counts, key=lambda value: (-kind_counts[value], value)) if kind_counts else None
        top_type = min(type_counts, key=lambda value: (-type_counts[value], value)) if type_counts else None
        enough = len(ring_places) >= MIN_FOR_TOP
        band_stats.append({
            "from": lower, "to": band, "places": len(ring_places), "street_km": street_km,
            "per_km": round(len(ring_places) / street_km, 1) if street_km else None,
            "top_kind": top_kind if enough else None,
            "top_kind_n": kind_counts[top_kind] if enough else None,
            "top_type": top_type if enough else None,
            "top_type_n": type_counts[top_type] if enough else None,
        })
        lower = band
    iso = {str(band): band_polygons(H, bands, band, lat0, lng0) for band in BANDS_MIN}
    output = {
        "slug": slug, "lat": lat0, "lng": lng0, "n_origin": len(origins),
        "origin_m": origin_m, "origin_fallback": origin_fallback,
        "n_nodes": H.number_of_nodes(), "n_edges_simple": H.number_of_edges(),
        "n_kept": n_kept, "bt_max": round(max(kept_values, default=0.0), 1),
        "segs": segs, "iso": iso, "node_kinds": node_kinds, "band_stats": band_stats,
        "places": {"total": len(places), "tied": len(tied), "off": off,
                   "b5": place_counts[5], "b10": place_counts[10], "b15": place_counts[15],
                   "b20": place_counts[20], "b25": place_counts[25]},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, slug + ".json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    km = {band: round(sum(d["length"] for u, v, d in H.edges(data=True)
                              if bands[(u, v)] is not None and bands[(u, v)] <= band) / 1000, 2)
          for band in BANDS_MIN}
    seconds = round(time.monotonic() - started, 1)
    row = {
        "slug": slug, "n_nodes": H.number_of_nodes(), "n_edges_simple": H.number_of_edges(),
        "n_kept": n_kept, "n_origin": len(origins), "origin_m": origin_m,
        "origin_fallback": origin_fallback, "bt_max": round(max(kept_values, default=0.0), 1),
        "km_b5": km[5], "km_b10": km[10], "km_b15": km[15], "km_b20": km[20], "km_b25": km[25],
        "places_total": len(places), "places_tied": len(tied), "places_off": off,
        "places_b5": place_counts[5], "places_b10": place_counts[10], "places_b15": place_counts[15],
        "places_b20": place_counts[20], "places_b25": place_counts[25],
        "seconds": seconds,
    }
    print(row, flush=True)
    return row


def main():
    slugs = sys.argv[1:]
    if not slugs:
        raise SystemExit("usage: python3 scripts/build_street_measures.py slug [slug ...]")
    rows = existing_summary()
    for slug in slugs:
        graph_path = os.path.join(GRAPH_DIR, slug + ".graphml")
        if not os.path.exists(graph_path):
            print(f"{slug}: missing graph", flush=True)
            continue
        out_path = os.path.join(OUT_DIR, slug + ".json")
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                current = json.load(f)
            if ("node_kinds" in current and "band_stats" in current
                    and len(current.get("segs", [])) == current.get("n_edges_simple")
                    and rows.get(slug, {}).get("n_origin", "") != ""):
                print(f"{slug}: already done", flush=True)
                continue
        rows[slug] = process(slug)
        write_summary(rows)


if __name__ == "__main__":
    main()
