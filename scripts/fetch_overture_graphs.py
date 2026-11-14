#!/usr/bin/env python3
"""Download Overture road segments and build walking street graphs."""

import os
from pathlib import Path


# Configuration required by codex/specs/12_overture_graphs.md.
REPO = Path(__file__).resolve().parents[1]
RADIUS_M = 1500
BUFFER_M = 300
K = 111320.0
OVERTURE = REPO / "cache/venv-overture/bin/overturemaps"
CERTS = REPO / "cache/venv-overture/lib/python3.13/site-packages/certifi/cacert.pem"
RAW_DIR = REPO / "cache/overture_raw"
GRAPH_DIR = REPO / "cache/graphs_overture"
MANIFEST = REPO / "outputs/overture_graphs_manifest.csv"
DROP_CLASSES = {"motorway", "cycleway"}
PAUSE_S = 3
RETRIES = 3
RETRY_WAIT_S = 30

os.environ.setdefault("MPLCONFIGDIR", str(REPO / "cache/mpl"))

import argparse
from collections import Counter
import csv
import json
import math
import subprocess
import time

import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, shape
from shapely.ops import substring, transform


FIELDS = [
    "slug", "status", "segments_raw", "roads", "dropped_class",
    "dropped_access", "dropped_sidewalk", "kept_denied_all", "subclasses",
    "n_nodes", "n_edges", "street_km",
    "share_osm_segments", "n_tomtom_segments",
    "nodes_removed_small_components", "named_edge_share", "seconds", "error",
]


def load_venues():
    with (REPO / "data/index.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def load_manifest():
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        return {row["slug"]: row for row in csv.DictReader(handle)}


def write_manifest(rows, venues):
    ordered = [rows[v["slug"]] for v in venues if v["slug"] in rows]
    with MANIFEST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)


def download(slug, feature_type, bbox):
    path = RAW_DIR / f"{slug}_{feature_type}.geojsonl"
    if path.exists():
        return path
    env = os.environ.copy()
    env["SSL_CERT_FILE"] = str(CERTS)
    command = [
        str(OVERTURE), "download", f"--bbox={bbox}", "-f", "geojsonseq",
        f"--type={feature_type}", "-o", str(path),
    ]
    for attempt in range(RETRIES):
        result = subprocess.run(command, env=env, check=False)
        if result.returncode == 0:
            return path
        if path.exists():
            path.unlink()
        if attempt < RETRIES - 1:
            time.sleep(RETRY_WAIT_S)
    raise RuntimeError(f"{feature_type} download failed with code {result.returncode}")


def read_features(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if line:
                yield json.loads(line)


def denied_on_foot(restrictions):
    for item in restrictions or []:
        if item.get("access_type") != "denied":
            continue
        when = item.get("when")
        if when is not None and when.get("heading") is None and "foot" in (when.get("mode") or []):
            return True
    return False


def denied_by_old_rule(restrictions):
    for item in restrictions or []:
        if item.get("access_type") != "denied":
            continue
        when = item.get("when")
        if when is None:
            return True
        mode = when.get("mode")
        mode_applies = mode in (None, "") or "foot" in mode
        if (when.get("heading") is None and mode_applies
                and when.get("using") is None and when.get("recognized") is None
                and when.get("vehicle") is None):
            return True
    return False


def source_dataset(properties):
    datasets = {s.get("dataset") for s in properties.get("sources") or []}
    datasets.discard(None)
    if datasets == {"OpenStreetMap"}:
        return "OpenStreetMap"
    if datasets == {"TomTom"}:
        return "TomTom"
    return "mixed"


def connector_id(item):
    return item.get("connector_id", item.get("id"))


def concatenate(first, second, node_xy):
    a = list(first.coords)
    b = list(second.coords)
    if math.dist(a[-1], node_xy) > math.dist(a[0], node_xy):
        a.reverse()
    if math.dist(b[0], node_xy) > math.dist(b[-1], node_xy):
        b.reverse()
    return LineString(a + b[1:])


def merged_value(first, second, separator=False):
    if first == second:
        return first
    values = []
    for value in (first, second):
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if candidate is not None and candidate not in values:
                values.append(candidate)
    if separator:
        return " / ".join(str(value) for value in values) or None
    return values


def simplify_graph(graph):
    while True:
        changed = False
        for node in list(graph.nodes):
            if graph.degree(node) != 2:
                continue
            neighbors = list(graph.neighbors(node))
            if len(neighbors) != 2:
                continue
            left, right = neighbors
            if left == right or graph.has_edge(left, right):
                continue
            first = graph.edges[left, node]
            second = graph.edges[node, right]
            geometry = concatenate(first["geometry"], second["geometry"], graph.nodes[node]["xy"])
            source = first["source_dataset"] if first["source_dataset"] == second["source_dataset"] else "mixed"
            graph.add_edge(
                left, right, length=first["length"] + second["length"], geometry=geometry,
                name=merged_value(first.get("name"), second.get("name"), separator=True),
                highway=merged_value(first["highway"], second["highway"]),
                source_dataset=source,
            )
            graph.remove_node(node)
            changed = True
            break
        if not changed:
            return


def sidewalk_or_crosswalk(properties):
    subclass = properties.get("subclass")
    if subclass is not None:
        return subclass in {"sidewalk", "crosswalk"}
    rules = properties.get("subclass_rules")
    return bool(rules) and all(rule.get("value") in {"sidewalk", "crosswalk"} for rule in rules)


def build_graph(segment_path, connector_path, lat, lng, access, sidewalks):
    cos_lat = math.cos(math.radians(lat))

    def to_local(x, y, z=None):
        return ((x - lng) * K * cos_lat, (y - lat) * K)

    def to_ll(x, y, z=None):
        return (lng + x / (K * cos_lat), lat + y / K)

    connector_positions = {}
    for feature in read_features(connector_path):
        feature_id = feature.get("id") or (feature.get("properties") or {}).get("id")
        geometry = feature.get("geometry")
        if feature_id is not None and geometry and geometry.get("type") == "Point":
            connector_positions[str(feature_id)] = tuple(geometry["coordinates"][:2])

    graph = nx.Graph()
    counts = {
        "segments_raw": 0, "roads": 0, "dropped_class": 0,
        "dropped_access": 0, "dropped_sidewalk": 0, "kept_denied_all": 0,
    }
    subclasses = Counter()
    for feature in read_features(segment_path):
        counts["segments_raw"] += 1
        properties = feature.get("properties") or {}
        if properties.get("subtype") != "road":
            continue
        counts["roads"] += 1
        highway = properties.get("class")
        if highway in DROP_CLASSES:
            counts["dropped_class"] += 1
            continue
        if access == "foot_denied" and denied_on_foot(properties.get("access_restrictions")):
            counts["dropped_access"] += 1
            continue
        if sidewalks == "drop" and sidewalk_or_crosswalk(properties):
            counts["dropped_sidewalk"] += 1
            continue
        if denied_by_old_rule(properties.get("access_restrictions")):
            counts["kept_denied_all"] += 1
        subclass = properties.get("subclass")
        if subclass is not None:
            subclasses[str(subclass)] += 1
        geometry_data = feature.get("geometry") or {}
        if geometry_data.get("type") != "LineString":
            continue
        local_line = transform(to_local, shape(geometry_data))
        connectors = properties.get("connectors") or []
        points = sorted(
            ((float(item["at"]), str(connector_id(item))) for item in connectors
             if connector_id(item) is not None and item.get("at") is not None),
            key=lambda value: value[0],
        )
        for (start_at, start_id), (end_at, end_id) in zip(points, points[1:]):
            if start_id == end_id:
                continue
            part = substring(local_line, start_at, end_at, normalized=True)
            if part.is_empty or part.geom_type != "LineString" or part.length == 0:
                continue
            for fraction, node_id in ((start_at, start_id), (end_at, end_id)):
                interpolated = transform(to_ll, local_line.interpolate(fraction, normalized=True))
                ll = connector_positions.get(node_id, (interpolated.x, interpolated.y))
                graph.add_node(node_id, xy=to_local(*ll), ll=ll)
            data = {
                "length": float(part.length), "geometry": part,
                "name": properties.get("names", {}).get("primary") if properties.get("names") else None,
                "highway": highway, "source_dataset": source_dataset(properties),
            }
            if not graph.has_edge(start_id, end_id) or data["length"] < graph[start_id][end_id]["length"]:
                graph.add_edge(start_id, end_id, **data)

    half_side = RADIUS_M + BUFFER_M
    outside = [node for node, data in graph.nodes(data=True)
               if abs(data["xy"][0]) > half_side or abs(data["xy"][1]) > half_side]
    graph.remove_nodes_from(outside)
    simplify_graph(graph)
    removed = 0
    if graph.number_of_nodes():
        largest = max(nx.connected_components(graph), key=len)
        removed = graph.number_of_nodes() - len(largest)
        graph = graph.subgraph(largest).copy()
    counts["subclasses"] = ";".join(f"{value}:{subclasses[value]}" for value in sorted(subclasses))
    return graph, counts, removed, to_ll


def save_graph(graph, path, to_ll):
    output = nx.MultiDiGraph(crs="epsg:4326")
    node_ids = {node: index for index, node in enumerate(sorted(graph.nodes))}
    for node in sorted(graph.nodes):
        ll = graph.nodes[node]["ll"]
        output.add_node(node_ids[node], x=float(ll[0]), y=float(ll[1]), street_count=graph.degree(node))
    for u, v, data in graph.edges(data=True):
        coords = [to_ll(x, y) for x, y in data["geometry"].coords]
        for start, end, oriented in ((u, v, coords), (v, u, list(reversed(coords)))):
            attrs = {
                "length": data["length"], "geometry": LineString(oriented),
                "highway": data["highway"], "source_dataset": data["source_dataset"],
            }
            if data.get("name"):
                attrs["name"] = data["name"]
            output.add_edge(node_ids[start], node_ids[end], key=0, **attrs)
    ox.save_graphml(output, path)
    return output


def process_venue(venue, access, sidewalks):
    started = time.monotonic()
    slug = venue["slug"]
    graph_path = GRAPH_DIR / f"{slug}.graphml"
    if graph_path.exists():
        return None
    lat, lng = float(venue["lat"]), float(venue["lng"])
    dlat = (RADIUS_M + BUFFER_M) / K
    dlng = dlat / math.cos(math.radians(lat))
    bbox = f"{lng - dlng:.5f},{lat - dlat:.5f},{lng + dlng:.5f},{lat + dlat:.5f}"
    segment_path = download(slug, "segment", bbox)
    connector_path = download(slug, "connector", bbox)
    graph, counts, removed, to_ll = build_graph(
        segment_path, connector_path, lat, lng, access, sidewalks,
    )
    if not graph.number_of_nodes():
        raise RuntimeError("no connected road graph remained")
    output = save_graph(graph, graph_path, to_ll)
    edges = list(graph.edges(data=True))
    row = {field: "" for field in FIELDS}
    row.update(counts)
    row.update({
        "slug": slug, "status": "saved", "n_nodes": output.number_of_nodes(),
        "n_edges": output.number_of_edges(),
        "street_km": f"{sum(d['length'] for _, _, d in edges) / 1000:.3f}",
        "share_osm_segments": f"{sum(d['source_dataset'] == 'OpenStreetMap' for _, _, d in edges) / len(edges):.3f}" if edges else "",
        "n_tomtom_segments": sum(d["source_dataset"] == "TomTom" for _, _, d in edges),
        "nodes_removed_small_components": removed,
        "named_edge_share": f"{sum(bool(d.get('name')) for _, _, d in edges) / len(edges):.3f}" if edges else "",
        "seconds": f"{time.monotonic() - started:.1f}", "error": "",
    })
    return row


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--access", choices=["foot_denied", "none"], default="foot_denied")
    parser.add_argument("--sidewalks", choices=["keep", "drop"], default="keep")
    parser.add_argument("--out-dir", type=Path, default=GRAPH_DIR)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("slugs", nargs="*")
    group.add_argument("--all", action="store_true", dest="all_venues")
    args = parser.parse_args()
    if not args.all_venues and not args.slugs:
        parser.error("provide one or more slugs, or --all")
    return args


def main():
    global GRAPH_DIR, MANIFEST
    args = parse_args()
    GRAPH_DIR = args.out_dir if args.out_dir.is_absolute() else REPO / args.out_dir
    MANIFEST = args.manifest if args.manifest.is_absolute() else REPO / args.manifest
    venues = load_venues()
    by_slug = {venue["slug"]: venue for venue in venues}
    selected = venues if args.all_venues else [by_slug[slug] for slug in args.slugs]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    rows = load_manifest()
    for position, venue in enumerate(selected, 1):
        try:
            row = process_venue(venue, args.access, args.sidewalks)
            if row is None:
                print(f"{position}/{len(selected)} {venue['slug']} skipped", flush=True)
            else:
                rows[venue["slug"]] = row
                print(f"{position}/{len(selected)} {venue['slug']} saved nodes={row['n_nodes']} edges={row['n_edges']} seconds={row['seconds']}", flush=True)
        except Exception as exc:
            row = {field: "" for field in FIELDS}
            row.update({"slug": venue["slug"], "status": "failed", "error": str(exc)})
            rows[venue["slug"]] = row
            print(f"{position}/{len(selected)} {venue['slug']} failed: {exc}", flush=True)
        write_manifest(rows, venues)
        if position < len(selected):
            time.sleep(PAUSE_S)


if __name__ == "__main__":
    main()
