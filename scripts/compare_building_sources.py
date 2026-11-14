#!/usr/bin/env python3
"""Compare Overture building footprint sources around selected venues."""

import argparse
import csv
import json
import math
import os
import subprocess
import time
from collections import Counter
from io import StringIO
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union


# Configuration required by codex/specs/10_building_sources.md.
REPO = Path(__file__).resolve().parents[1]
RADIUS_M = 1500
K = 111320.0
OVERTURE = REPO / "cache/venv-overture/bin/overturemaps"
CERTS = REPO / "cache/venv-overture/lib/python3.13/site-packages/certifi/cacert.pem"
BUILD_DIR = REPO / "cache/buildings"
OUT = REPO / "outputs/building_sources_compare.csv"
ALL_OUT = REPO / "outputs/built_proportion_all.csv"
DEFAULT_SLUGS = [
    "11-de-novembro-stadium",
    "benjamin-mkapa-national-stadium",
    "maracan-stadium",
    "wembley-stadium",
    "bmo-field-toronto",
]

FIELDS = [
    "slug",
    "status",
    "n_all",
    "n_osm",
    "n_google",
    "n_microsoft",
    "n_other",
    "ratio_all",
    "ratio_osm",
    "osm_share_of_built_area",
    "site_builtArea",
]
ALL_FIELDS = FIELDS + ["seconds", "overture_release"]


def load_index():
    with (REPO / "data/index.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def load_inputs(slugs):
    index = load_index()
    built_areas = {item["slug"]: item.get("builtArea", "") for item in index}
    venues = {}
    for slug in slugs:
        with (REPO / "data" / f"{slug}.json").open(encoding="utf-8") as handle:
            venue = json.load(handle)
        venues[slug] = (venue["m"]["latitude"], venue["m"]["longitude"])
    return venues, built_areas


def download_if_needed(slug, lat, lng, retry=False):
    path = BUILD_DIR / f"{slug}.geojsonl"
    if path.exists():
        return path, True

    dlat = RADIUS_M / K
    dlng = dlat / math.cos(math.radians(lat))
    bbox = f"{lng - dlng:.5f},{lat - dlat:.5f},{lng + dlng:.5f},{lat + dlat:.5f}"
    env = os.environ.copy()
    env["SSL_CERT_FILE"] = str(CERTS)
    command = [
        str(OVERTURE),
        "download",
        f"--bbox={bbox}",
        "-f",
        "geojsonseq",
        "--type=building",
        "-o",
        str(path),
    ]
    result = subprocess.run(command, env=env, check=False)
    if result.returncode == 0:
        return path, True
    if retry:
        time.sleep(20)
        result = subprocess.run(command, env=env, check=False)
    return path, result.returncode == 0


def process_venue(slug, lat, lng, built_area, retry=False):
    path, downloaded = download_if_needed(slug, lat, lng, retry=retry)
    if not downloaded:
        return {"slug": slug, "status": "failed", "site_builtArea": built_area}, Counter()

    circle = Point(0, 0).buffer(RADIUS_M, 128)
    cos_lat = math.cos(math.radians(lat))
    clipped = []
    osm_clipped = []
    source_counts = Counter()

    def to_local(x, y, z=None):
        return ((x - lng) * K * cos_lat, (y - lat) * K)

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            properties = feature.get("properties") or {}
            if properties.get("is_underground") is True:
                continue
            geometry_data = feature.get("geometry") or {}
            if geometry_data.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            sources = properties.get("sources") or []
            source = sources[0].get("dataset", "unknown") if sources else "unknown"
            geometry = transform(to_local, shape(geometry_data))
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            geometry = geometry.intersection(circle)
            if geometry.is_empty:
                continue
            clipped.append(geometry)
            source_counts[source] += 1
            if source == "OpenStreetMap":
                osm_clipped.append(geometry)

    all_area = unary_union(clipped).area if clipped else 0.0
    osm_area = unary_union(osm_clipped).area if osm_clipped else 0.0
    ratio_all = round(all_area / circle.area, 4)
    ratio_osm = round(osm_area / circle.area, 4)
    share = "" if ratio_all == 0 else f"{ratio_osm / ratio_all:.3f}"
    known = {
        "OpenStreetMap",
        "Google Open Buildings",
        "Microsoft ML Buildings",
    }
    n_other = sum(count for source, count in source_counts.items() if source not in known)
    row = {
        "slug": slug,
        "status": "ok",
        "n_all": len(clipped),
        "n_osm": source_counts["OpenStreetMap"],
        "n_google": source_counts["Google Open Buildings"],
        "n_microsoft": source_counts["Microsoft ML Buildings"],
        "n_other": n_other,
        "ratio_all": f"{ratio_all:.4f}",
        "ratio_osm": f"{ratio_osm:.4f}",
        "osm_share_of_built_area": share,
        "site_builtArea": built_area,
    }
    return row, source_counts


def write_csv(rows, path=OUT, fields=FIELDS, replace=False):
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    content = buffer.getvalue()

    # A restart leaves an already completed result untouched.
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return
        if not replace:
            raise RuntimeError(f"Refusing to overwrite changed output: {path}")
    path.write_text(content, encoding="utf-8")


def overture_release(slug):
    state_path = BUILD_DIR / f"{slug}.geojsonl.state"
    if not state_path.exists():
        return ""
    with state_path.open(encoding="utf-8") as handle:
        return json.load(handle).get("last_release", "")


def run_all():
    index = load_index()
    slugs = [item["slug"] for item in index]
    venues, built_areas = load_inputs(slugs)
    existing = {}
    if ALL_OUT.exists():
        with ALL_OUT.open(encoding="utf-8", newline="") as handle:
            existing = {row["slug"]: row for row in csv.DictReader(handle)}
    rows = [existing[slug] for slug in slugs if slug in existing]
    for position, slug in enumerate(slugs, 1):
        started = time.monotonic()
        lat, lng = venues[slug]
        row, unused_sources = process_venue(
            slug, lat, lng, built_areas.get(slug, ""), retry=True
        )
        row["seconds"] = f"{time.monotonic() - started:.1f}"
        row["overture_release"] = overture_release(slug)
        existing[slug] = row
        rows = [existing[item_slug] for item_slug in slugs if item_slug in existing]
        write_csv(rows, path=ALL_OUT, fields=ALL_FIELDS, replace=True)
        print(
            f"{position}/{len(slugs)}",
            slug,
            row["status"],
            row.get("n_all", ""),
            row.get("ratio_all", ""),
            row.get("ratio_osm", ""),
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", dest="all_venues")
    parser.add_argument("slugs", nargs="*")
    return parser.parse_args()


def print_table(rows):
    values = [[str(row.get(field, "")) for field in FIELDS] for row in rows]
    widths = [
        max(len(FIELDS[index]), *(len(row[index]) for row in values))
        for index in range(len(FIELDS))
    ]
    print("  ".join(field.ljust(widths[index]) for index, field in enumerate(FIELDS)))
    print("  ".join("-" * width for width in widths))
    for row in values:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def main():
    args = parse_args()
    if args.all_venues:
        run_all()
        return

    slugs = DEFAULT_SLUGS + [slug for slug in args.slugs if slug not in DEFAULT_SLUGS]
    venues, built_areas = load_inputs(slugs)
    rows = []
    all_sources = Counter()
    for slug in slugs:
        lat, lng = venues[slug]
        row, sources = process_venue(slug, lat, lng, built_areas.get(slug, ""))
        rows.append(row)
        all_sources.update(sources)
    write_csv(rows, replace=bool(args.slugs))
    print_table(rows)
    print("\nDistinct sources:")
    for source in sorted(all_sources):
        print(f"{source}: {all_sources[source]}")


if __name__ == "__main__":
    main()
