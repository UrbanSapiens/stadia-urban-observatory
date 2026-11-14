#!/usr/bin/env python3
"""Build a hand-review table for venue Wikidata matches and architects."""

import csv
import difflib
import json
import os
import re
import subprocess
import time
import unicodedata


# ---------------- config ----------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "venue-architect-lookup/1.0"
SEARCH_KM = 1.0
SIM_ACCEPT = 0.60
NEAR_KM = 0.30
PAUSE_S = 1.5
VENUE_CLASS = "Q1076486"
CANDIDATE_CACHE = os.path.join(ROOT, "cache", "wikidata_candidates.json")
OUT = os.path.join(ROOT, "outputs", "architects_review.csv")
INDEX = os.path.join(ROOT, "data", "index.json")
EXISTING = os.path.join(ROOT, "outputs", "architects_wikidata.csv")
# ----------------------------------------

FIELDS = [
    "slug", "name", "wikidata_item", "wikidata_label", "match_source",
    "match_status", "match_sim", "match_dist_km", "other_candidates",
    "wd_opened_year", "wd_inception_year", "architects", "n_architects",
    "keep", "source",
]
DROP_WORDS = ("stadium", "stadion", "estadio", "stade", "arena", "park", "field")


def sparql(query):
    """Run one curl GET query, retrying only throttling and server failures."""
    for attempt in range(3):
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-G", ENDPOINT,
                    "-H", "Accept: application/sparql-results+json",
                    "-A", USER_AGENT,
                    "--data-urlencode", "query=" + query,
                    "-w", "\n%{http_code}",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"curl failed with exit code {error.returncode}") from error
        body, separator, status_text = result.stdout.rpartition("\n")
        if not separator or not status_text.isdigit():
            raise RuntimeError("curl response did not include an HTTP status code")
        status = int(status_text)
        if status == 200:
            return json.loads(body)
        if status == 400:
            raise RuntimeError("Wikidata rejected the SPARQL query with HTTP 400")
        if status == 429 or 500 <= status <= 599:
            if attempt < 2:
                time.sleep(30)
                continue
        raise RuntimeError(f"Wikidata returned HTTP {status}")


def candidate_query(venue):
    point = f"Point({venue['lng']} {venue['lat']})"
    return f'''SELECT ?item ?itemLabel (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases)
       (MIN(?d) AS ?distance)
WHERE {{
  SERVICE wikibase:around {{
    ?item wdt:P625 ?loc .
    bd:serviceParam wikibase:center "{point}"^^geo:wktLiteral ;
                    wikibase:radius "{SEARCH_KM}" ;
                    wikibase:distance ?d .
  }}
  ?item wdt:P31/wdt:P279* wd:{VENUE_CLASS} .
  ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "en")
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en") }}
}}
GROUP BY ?item ?itemLabel'''


def raw_candidates(data):
    candidates = []
    for binding in data["results"]["bindings"]:
        candidates.append({
            "item": binding["item"]["value"].rsplit("/", 1)[-1],
            "label": binding["itemLabel"]["value"],
            "aliases": binding.get("aliases", {}).get("value", "").split("|")
            if binding.get("aliases", {}).get("value") else [],
            "distance": float(binding["distance"]["value"]),
        })
    return candidates


def save_cache(cache):
    os.makedirs(os.path.dirname(CANDIDATE_CACHE), exist_ok=True)
    temporary = CANDIDATE_CACHE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, CANDIDATE_CACHE)


def normalise(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "".join(word for word in words if word not in DROP_WORDS)


def choose(name, candidates):
    target = normalise(name)
    scored = []
    for candidate in candidates:
        comparisons = [candidate["label"]] + candidate["aliases"]
        candidate = dict(candidate)
        candidate["sim"] = max(
            (difflib.SequenceMatcher(None, target, normalise(value)).ratio()
             for value in comparisons),
            default=0.0,
        )
        scored.append(candidate)
    scored.sort(key=lambda candidate: (-candidate["sim"], candidate["distance"]))
    if not scored:
        return "none", None, []
    best = scored[0]
    near_count = sum(candidate["distance"] <= NEAR_KM for candidate in scored)
    status = "accepted" if best["sim"] >= SIM_ACCEPT or near_count == 1 else "review"
    return status, best, scored[1:4]


def architect_query(items):
    values = " ".join("wd:" + item for item in items)
    return f'''SELECT ?item ?aLabel ?openedYear ?inceptionYear WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{
    ?item wdt:P84 ?a .
    ?a rdfs:label ?aLabel .
    FILTER(LANG(?aLabel) = "en")
  }}
  OPTIONAL {{ ?item wdt:P1619 ?opened . BIND(YEAR(?opened) AS ?openedYear) }}
  OPTIONAL {{ ?item wdt:P571 ?inception . BIND(YEAR(?inception) AS ?inceptionYear) }}
}}'''


def get_architects(items):
    found = {item: {"architects": set(), "opened": set(), "inception": set()}
             for item in items}
    for start in range(0, len(items), 100):
        batch = items[start:start + 100]
        data = sparql(architect_query(batch))
        for binding in data["results"]["bindings"]:
            item = binding["item"]["value"].rsplit("/", 1)[-1]
            label = binding.get("aLabel", {}).get("value", "")
            if label and not re.fullmatch(r"Q\d+", label):
                found[item]["architects"].add(label)
            if binding.get("openedYear", {}).get("value"):
                found[item]["opened"].add(binding["openedYear"]["value"])
            if binding.get("inceptionYear", {}).get("value"):
                found[item]["inception"].add(binding["inceptionYear"]["value"])
    return found


def joined(values):
    return "; ".join(sorted(values, key=lambda value: (int(value) if value.isdigit() else value)))


def render_candidate(candidate):
    return f"{candidate['label']} ({candidate['distance']:.2f} km, sim {candidate['sim']:.2f})"


def write_output(rows):
    temporary = OUT + ".tmp"
    with open(temporary, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if os.path.exists(OUT):
        with open(OUT, "rb") as old, open(temporary, "rb") as new:
            if old.read() == new.read():
                os.remove(temporary)
                return False
        raise RuntimeError(f"refusing to overwrite changed output: {OUT}")
    os.replace(temporary, OUT)
    return True


def main():
    with open(INDEX, encoding="utf-8") as handle:
        venues = json.load(handle)
    with open(EXISTING, newline="", encoding="utf-8") as handle:
        paper = {row["slug"]: row for row in csv.DictReader(handle)}
    if os.path.exists(CANDIDATE_CACHE):
        with open(CANDIDATE_CACHE, encoding="utf-8") as handle:
            cache = json.load(handle)
    else:
        cache = {}

    candidate_queries = 0
    initial_timeouts = 0
    for venue in venues:
        slug = venue["slug"]
        if slug in paper or slug in cache:
            continue
        try:
            cache[slug] = raw_candidates(sparql(candidate_query(venue)))
        except subprocess.TimeoutExpired as error:
            initial_timeouts += 1
            if candidate_queries < 3 and initial_timeouts == 3:
                raise RuntimeError("candidate query timed out for the first 3 venues") from error
            raise
        candidate_queries += 1
        save_cache(cache)
        time.sleep(PAUSE_S)

    selections = {}
    items = set(row["wikidata_item"] for row in paper.values())
    for venue in venues:
        if venue["slug"] not in paper:
            selection = choose(venue["name"], cache[venue["slug"]])
            selections[venue["slug"]] = selection
            if selection[1]:
                items.add(selection[1]["item"])
    details = get_architects(sorted(items))

    rows = []
    for venue in venues:
        slug = venue["slug"]
        if slug in paper:
            item = paper[slug]["wikidata_item"]
            label = paper[slug]["wikidata_label"]
            source = "paper"
            status, sim, distance, others = "accepted", "", "", ""
        else:
            status, best, other = selections[slug]
            item = best["item"] if best else ""
            label = best["label"] if best else ""
            source = "new"
            sim = f"{best['sim']:.2f}" if best else ""
            distance = f"{best['distance']:.2f}" if best else ""
            others = "; ".join(render_candidate(candidate) for candidate in other)
        info = details.get(item, {"architects": set(), "opened": set(), "inception": set()})
        architects = joined(info["architects"])
        rows.append({
            "slug": slug, "name": venue["name"], "wikidata_item": item,
            "wikidata_label": label, "match_source": source, "match_status": status,
            "match_sim": sim, "match_dist_km": distance, "other_candidates": others,
            "wd_opened_year": joined(info["opened"]),
            "wd_inception_year": joined(info["inception"]), "architects": architects,
            "n_architects": len(info["architects"]),
            "keep": architects if status == "accepted" else "",
            "source": f"https://www.wikidata.org/wiki/{item}#P84" if item else "",
        })
    rows.sort(key=lambda row: row["slug"])
    created = write_output(rows)
    print(f"candidate queries: {candidate_queries}")
    print(f"wrote {os.path.relpath(OUT, ROOT)}" if created else
          f"identical output already exists: {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
