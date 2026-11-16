#!/usr/bin/env python3
"""Build venue-page building, resident, and height layers."""

import argparse
import csv
import gzip
import json
import math
import random
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from shapely.geometry import Polygon, box, shape
from shapely.strtree import STRtree


# Configuration required by codex/specs/43_site_layers.md.
REPO = Path(__file__).resolve().parents[1]
RADIUS_M = 1500
CELL_M = 100
GRID_N = 30
MIN_VALID_SHARE = 0.5
INDEX = REPO / "data/index.json"
BUILDINGS = REPO / "cache/buildings"
GHS = REPO / "cache/ghs_built"
OUT = REPO / "layers"
ERRORS = REPO / "outputs/site_layers_errors.csv"
MOCKUP = REPO / "proposals/layers-v2.html"
MOCKUP_SLUGS = ["maracan-stadium", "wembley-stadium", "accra-sports-stadium",
                "childrens-mercy-park", "bc-place-stadium-vancouver"]
E2 = 0.00669437999014
SMOD_LABELS = {30: "Urban centre", 23: "Dense urban cluster",
               22: "Semi-dense urban cluster", 21: "Suburban or peri-urban",
               13: "Rural cluster", 12: "Low density rural",
               11: "Very low density rural", 10: "Water"}


def significant_two(value):
    return 0.0 if value == 0 else float(f"{value:.2g}")


def valid(array, nodata):
    answer = np.isfinite(array)
    if nodata is not None:
        answer &= ~np.isclose(array, nodata)
    return answer


def local_tools(venue):
    crs = f"+proj=aeqd +lat_0={venue['lat']} +lon_0={venue['lng']} +datum=WGS84 +units=m"
    to_local = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    ghs_wgs = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)
    wgs_ghs = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)
    return to_local, ghs_wgs, wgs_ghs


def source_polygons(dataset, ghs_wgs, to_local):
    polygons, areas = [], []
    for row in range(dataset.height):
        for col in range(dataset.width):
            corners = [dataset.transform * (col, row), dataset.transform * (col + 1, row),
                       dataset.transform * (col + 1, row + 1), dataset.transform * (col, row + 1)]
            polygon = Polygon([to_local.transform(*ghs_wgs.transform(x, y)) for x, y in corners])
            polygons.append(polygon)
            areas.append(polygon.area)
    return polygons, np.asarray(areas)


def target_cells():
    return [box(-RADIUS_M + i * CELL_M, -RADIUS_M + j * CELL_M,
                -RADIUS_M + (i + 1) * CELL_M, -RADIUS_M + (j + 1) * CELL_M)
            for j in range(GRID_N) for i in range(GRID_N)]


def target_index(x, y):
    if not (-RADIUS_M <= x < RADIUS_M and -RADIUS_M <= y < RADIUS_M):
        return None
    return int((y + RADIUS_M) // CELL_M) * GRID_N + int((x + RADIUS_M) // CELL_M)


def q_scales(lat):
    p = math.radians(lat)
    my = 111132.92 - 559.82 * math.cos(2*p) + 1.175 * math.cos(4*p) - .0023 * math.cos(6*p)
    mx = 111412.84 * math.cos(p) - 93.5 * math.cos(3*p) + .118 * math.cos(5*p)
    return round(mx, 3), round(my, 3)


def settlement(dataset, venue, wgs_ghs):
    row, col = dataset.index(*wgs_ghs.transform(venue["lng"], venue["lat"]))
    raw = float(dataset.read(1)[row, col])
    code = None if not np.isfinite(raw) or (dataset.nodata is not None and np.isclose(raw, dataset.nodata)) else int(raw)
    return code, SMOD_LABELS.get(code, "")


def read_records(slug):
    with (BUILDINGS / f"{slug}.geojsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if line:
                yield json.loads(line)


def included_record(record, venue):
    data = record.get("geometry") or {}
    if data.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    geometry = shape(data)
    centroid = geometry.centroid
    dx = (centroid.x - venue["lng"]) * 111320.0 * math.cos(math.radians(venue["lat"]))
    dy = (centroid.y - venue["lat"]) * 111320.0
    return geometry if math.hypot(dx, dy) <= RADIUS_M else None


def encode_polygon(polygon, venue, mx, my, cell):
    rounded = []
    for lng, lat in list(polygon.exterior.coords)[:-1]:
        point = (round((lng - venue["lng"]) * mx * 10), round((lat - venue["lat"]) * my * 10))
        if not rounded or point != rounded[-1]:
            rounded.append(point)
    if len(set(rounded)) < 3:
        return None
    result = [cell, rounded[0][0], rounded[0][1]]
    for previous, point in zip(rounded, rounded[1:]):
        result.extend([point[0] - previous[0], point[1] - previous[1]])
    return result


def footprints(slug, venue, to_local, heights, h_nodata, mx, my):
    entries, sources, source_vertices = [], Counter(), []
    drawn = dropped = 0
    for record in read_records(slug):
        geometry = included_record(record, venue)
        if geometry is None:
            continue
        drawn += 1
        listed = (record.get("properties") or {}).get("sources") or []
        first = listed[0] if listed else {}
        sources[(first.get("dataset", ""), first.get("license") or "not stated")] += 1
        polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
        for polygon in polygons:
            centroid = polygon.centroid
            cell = target_index(*to_local.transform(centroid.x, centroid.y))
            cell_value = -1 if cell is None else cell
            encoded = encode_polygon(polygon, venue, mx, my, cell_value)
            if encoded is None:
                dropped += 1
                continue
            entries.append(encoded)
            source_vertices.append(list(polygon.exterior.coords)[:-1])
    return entries, drawn, dropped, sources, source_vertices


def prepare_venue(source):
    started = time.time()
    venue = {"slug": source["slug"], "lat": source["lat"], "lng": source["lng"]}
    slug = venue["slug"]
    to_local, ghs_wgs, wgs_ghs = local_tools(venue)
    targets, included = target_cells(), np.array([
        math.hypot(-1450 + (t % 30) * 100, -1450 + (t // 30) * 100) <= RADIUS_M
        for t in range(900)])
    tree = STRtree(targets)
    with rasterio.open(GHS / "pop" / f"{slug}.tif") as pop_ds, \
         rasterio.open(GHS / "anbh" / f"{slug}.tif") as anbh_ds, \
         rasterio.open(GHS / "agbh" / f"{slug}.tif") as agbh_ds, \
         rasterio.open(GHS / "smod" / f"{slug}.tif") as smod_ds:
        if not (pop_ds.transform == anbh_ds.transform == agbh_ds.transform and
                pop_ds.shape == anbh_ds.shape == agbh_ds.shape):
            raise RuntimeError(f"GHSL grids differ for {slug}")
        source_cells, source_areas = source_polygons(pop_ds, ghs_wgs, to_local)
        pop = pop_ds.read(1).astype(float).ravel()
        anbh = anbh_ds.read(1).astype(float).ravel()
        agbh = agbh_ds.read(1).astype(float).ravel()
        pop_valid = valid(pop, pop_ds.nodata)
        h_valid = valid(anbh, anbh_ds.nodata) & valid(agbh, agbh_ds.nodata)
        built_share = np.zeros_like(anbh)
        positive = h_valid & (anbh > 0)
        built_share[positive] = agbh[positive] / anbh[positive]
        pop_total = np.zeros(900); pop_coverage = np.zeros(900)
        volume = np.zeros(900); built = np.zeros(900); h_coverage = np.zeros(900)
        big = box(-RADIUS_M, -RADIUS_M, RADIUS_M, RADIUS_M)
        direct_pop = direct_volume = direct_built = 0.0
        for s, polygon in enumerate(source_cells):
            for target in tree.query(polygon):
                t = int(target); area = polygon.intersection(targets[t]).area
                if area <= 0: continue
                if pop_valid[s]:
                    pop_coverage[t] += area; pop_total[t] += pop[s] * area / source_areas[s]
                if h_valid[s]:
                    h_coverage[t] += area; volume[t] += agbh[s] * area; built[t] += built_share[s] * area
            area = polygon.intersection(big).area
            if pop_valid[s]: direct_pop += pop[s] * area / source_areas[s]
            if h_valid[s]: direct_volume += agbh[s] * area; direct_built += built_share[s] * area
        heights = np.divide(volume, built, out=np.zeros_like(volume), where=built > 0)
        pop_nodata = pop_coverage < MIN_VALID_SHARE * 10000
        h_nodata = h_coverage < MIN_VALID_SHARE * 10000
        code, label = settlement(smod_ds, venue, wgs_ghs)
    mx, my = q_scales(venue["lat"])
    fp, drawn, dropped, sources, source_vertices = footprints(slug, venue, to_local, heights, h_nodata, mx, my)
    pop_out = [None if not included[t] or pop_nodata[t] else round(float(pop_total[t]), 1) for t in range(900)]
    h_out = [None if not included[t] or h_nodata[t] else round(float(heights[t]), 1) for t in range(900)]
    positive_h = heights[included & ~h_nodata & (heights > 0)]
    residents_raw = float(pop_total[included & ~pop_nodata].sum())
    data = {"slug": slug, "lat0": source["lat"], "lng0": source["lng"], "mx": mx, "my": my,
            "pop": pop_out, "h": h_out, "fp": fp,
            "totals": {"footprints": drawn, "fp_entries": len(fp), "residents": round(residents_raw, 1),
                       "median_h": round(float(np.median(positive_h)), 1) if positive_h.size else None,
                       "tallest_h": round(float(positive_h.max()), 1) if positive_h.size else None,
                       "smod": code, "settlement": label}}
    expected_area = 10000 * (1-E2) / (1-E2*math.sin(math.radians(venue["lat"]))**2)**2
    check = {"area_min": float(source_areas.min()), "area_max": float(source_areas.max()),
             "area_expected": expected_area, "pop_grid": float(pop_total.sum()), "pop_direct": direct_pop,
             "volume_grid": float(volume.sum()), "volume_direct": direct_volume,
             "built_grid": float(built.sum()), "built_direct": direct_built,
             "residents_raw": residents_raw, "dropped": dropped, "sources": sources,
             "source_vertices": source_vertices, "seconds": time.time()-started}
    return data, check


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def all_files():
    answer = []
    for path in OUT.glob("*.json"):
        if path.name != "index.json":
            try: answer.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError): pass
    return sorted(answer, key=lambda item: item["slug"])


def regenerate_summary(files, source_counts=None):
    pop = [v for item in files for v in item["pop"] if v is not None and v > 0]
    heights = [v for item in files for v in item["h"] if v is not None and v > 0]
    top_pop = significant_two(float(np.percentile(pop, 98))) if pop else 0
    top_h = significant_two(float(np.percentile(heights, 98))) if heights else 0
    write_json(OUT / "index.json", {"venues": [x["slug"] for x in files], "top_pop": top_pop,
               "top_h": top_h, "release": "Overture Maps 2026-10-21", "generated": date.today().isoformat()})
    if source_counts is None:
        source_counts = Counter()
        venues = {x["slug"]: x for x in json.loads(INDEX.read_text(encoding="utf-8"))}
        for item in files:
            for record in read_records(item["slug"]):
                if included_record(record, venues[item["slug"]]) is not None:
                    listed = (record.get("properties") or {}).get("sources") or []
                    first = listed[0] if listed else {}
                    source_counts[(first.get("dataset", ""), first.get("license") or "not stated")] += 1
    total = sum(source_counts.values())
    rows = "\n".join(f"| {dataset} | {license_} | {count} | {100*count/total:.1f}% |"
                     for (dataset, license_), count in sorted(source_counts.items(), key=lambda x: (-x[1], x[0])))
    text = f"""# Building footprints in this folder

Source: Overture Maps Foundation, buildings theme, release 2026-10-21. Each footprint
keeps the license stated in its Overture record. The table counts footprints by the
first source listed in the record.

| Source dataset | License in the record | Footprints | Share |
|---|---|---|---|
{rows}

Total: {total} footprints at {len(files)} venues.

Google Open Buildings states no license inside the Overture records. Its own page
(https://sites.research.google/gr/open-buildings/, checked 2026-11-16) releases the data under
CC BY 4.0 or ODbL v1.0, at the user's choice.

Residents and building height (the `pop` and `h` arrays): GHSL, European Commission,
Joint Research Centre, GHS-POP 2020 and GHS-BUILT-H 2018, release R2023A, CC BY 4.0.
Adapted: the values were recomputed from the source grid onto 100 m squares.

These files are not covered by the CC BY 4.0 license of the rest of the repository
where the record states another license.
"""
    (OUT / "SOURCES.md").write_text(text, encoding="utf-8")
    mock = re.search(r"const data=(.*?);\nconst SAT_TILES", MOCKUP.read_text(encoding="utf-8"), re.S)
    mock_data = json.loads(mock.group(1)) if mock else {"top_pop": None, "top_h": None}
    print(f"top_pop {top_pop}; mock-up {mock_data['top_pop']}")
    print(f"top_h {top_h}; mock-up {mock_data['top_h']}")
    return top_pop, top_h, total


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--venues"); group.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); ERRORS.parent.mkdir(parents=True, exist_ok=True)
    items = json.loads(INDEX.read_text(encoding="utf-8")); by_slug = {x["slug"]: x for x in items}
    slugs = list(by_slug) if args.all else [x for x in args.venues.split(",") if x]
    errors, checks, source_counts = [], {}, Counter()
    for number, slug in enumerate(slugs, 1):
        path = OUT / f"{slug}.json"
        if path.exists() and not args.force:
            print(f"SKIP {number}/{len(slugs)} {slug}", flush=True); continue
        try:
            if slug not in by_slug: raise RuntimeError("slug is not in data/index.json")
            data, check = prepare_venue(by_slug[slug]); write_json(path, data)
            checks[slug] = check; source_counts.update(check["sources"])
            print(f"DONE {number}/{len(slugs)} {slug}: {check['seconds']:.1f} seconds", flush=True)
        except Exception as error:
            errors.append((slug, f"{type(error).__name__}: {error}")); print(f"ERROR {slug}: {errors[-1][1]}", flush=True)
    with ERRORS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["slug", "reason"]); writer.writerows(errors)
    files = all_files(); regenerate_summary(files, None)
    print(f"COVERAGE venue files {len(files)}; errors {len(errors)}")
    for slug, check in checks.items():
        ok = abs(check['area_min']-check['area_expected']) <= 1 and abs(check['area_max']-check['area_expected']) <= 1
        print(f"CHECK 1 {'PASS' if ok else 'FAIL'} {slug}: expected {check['area_expected']:.6f}; min {check['area_min']:.6f}; max {check['area_max']:.6f}")


if __name__ == "__main__":
    main()
