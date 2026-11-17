# Stadia Urban Observatory

A research platform mapping the urban fabric around major event stadiums —
density patterns, place distributions, and neighbourhood profiles at venues from
the FIFA World Cup, the Olympic Games, UEFA Euro, MLS, and China's stadium
diplomacy programme.

**Live site:** https://urbansapiens.github.io/stadia-urban-observatory/

The site counts visits with GoatCounter (page, referrer, country), without cookies or personal data.

A sibling of the [Goiás Violence Observatory](https://github.com/UrbanSapiens/goias-violence-observatory).

---

## What's here

**247 unique venues across 58 countries.** The site shows each venue as counts
(`agg/`), not as individual places. The per-place working records are kept
locally and are not part of this repository.

Event coverage: 94 FIFA World Cup venues · 65 UEFA Euro · 40 China stadium
diplomacy · 36 MLS · 13 Olympic Games · 3 MLB.

Each stadium record carries an identity block (`m`) — name, city, country,
capacity, opening year, primary sport, events — plus the 4-D urban model
metrics: place diversity, median distance to amenities, built-area ratio,
intersection density, and block length.

## Layout

```
index.html            the site: landing, map and venue pages (reads agg/ only)
agg/index.json        venue list, study distributions, atlas plates
agg/<slug>.json       one venue as counts: per grid cell and kind, per distance ring
layers/<slug>.json    building footprints and 100 m squares of residents and height (see layers/SOURCES.md)
data/index.json       venue list: name, country, coordinates, events
data/geo/             country outlines (Natural Earth 1:110m, public domain)
scripts/              audit, sync, aggregate build, collection dates, architects
outputs/              collection_dates.csv, architects_wikidata.csv (with sources)
Stadia_database.xlsx  spreadsheet mirror of the venue records
DATA_AUDIT.md         correction procedure and log
```

Pages have addresses: `#/map`, `#/map/<slug>`, `#/venue/<slug>`.

## Working on the data

Read [`DATA_AUDIT.md`](DATA_AUDIT.md) first. The short version:

```bash
python3 scripts/audit_data.py        # must exit clean: fix every ERROR
python3 scripts/sync_index.py        # data/index.json
python3 scripts/build_site_data.py   # agg/: what the site shows; stops if a sentence is not true
python3 scripts/audit_data.py        # re-run, expect 0 ERROR
```

Commit `data/index.json` and `agg/` together. The site reads `agg/`, so a
data change that is not followed by `build_site_data.py` does not reach it.
The per-venue files `data/<slug>.json` and the cleaned place database are
local only, so the build cannot be run from a fresh clone.

`build_site_data.py` writes every sentence shown at the top of a venue page and
re-derives each one from the records before saving. The audit checks event
claims against ground truth, duplicate venues, placeholder metrics, impossible
opening years, and the country field against the coordinates.

## License

- **Code** (`scripts/`, dashboard source) — MIT, see [`LICENSE`](LICENSE)
- **Data** (`agg/`, `data/index.json`, `Stadia_database.xlsx`) — CC BY 4.0, see [`LICENSE-DATA`](LICENSE-DATA)
