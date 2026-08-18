# Stadia Urban Observatory

A research platform mapping the urban fabric around major event stadiums —
density patterns, place distributions, and neighbourhood profiles at venues from
the FIFA World Cup, the Olympic Games, UEFA Euro, MLS, and China's stadium
diplomacy programme.

**Live site:** https://urbansapiens.github.io/stadia-urban-observatory/

A sibling of the [Goiás Violence Observatory](https://github.com/UrbanSapiens/goias-violence-observatory).

---

## What's here

**247 unique venues across 55 countries**, held in 280 per-stadium JSON files
under `data/` — 32 venues were surveyed twice, and both passes are kept so the
dashboard can show the same ground at two points in time.

Event coverage: 94 FIFA World Cup venues · 65 UEFA Euro · 40 China stadium
diplomacy · 36 MLS · 13 Olympic Games · 3 MLB.

Each stadium record carries an identity block (`m`) — name, city, country,
capacity, opening year, primary sport, events — plus the 4-D urban model
metrics: place diversity, median distance to amenities, built-area ratio,
intersection density, and block length.

## Layout

```
index.html          the dashboard (data embedded as the IX array)
data/<slug>.json    one file per stadium; the m block is the source of truth
data/index.json     roll-up consumed by the build, not by the live site
scripts/            audit_data.py, sync_index.py
Stadia_database.xlsx  spreadsheet mirror of the same records
DATA_AUDIT.md       correction procedure and log
```

The raw survey archive — PDFs, satellite imagery, shapefiles, GraphML networks,
roughly 28 GB — is kept outside the repository. Only derived records are
versioned here.

## Working on the data

Read [`DATA_AUDIT.md`](DATA_AUDIT.md) first. The short version:

```bash
python3 scripts/audit_data.py    # must exit clean — fix every ERROR
python3 scripts/sync_index.py    # propagates into data/index.json AND index.html
python3 scripts/audit_data.py    # re-run, expect 0 ERROR
```

**The live site does not fetch `data/index.json`.** It reads the `IX` array
embedded in `index.html`. Skipping `sync_index.py` leaves the public site
showing stale numbers, so `data/` and `index.html` must be committed together.

The audit checks event claims against ground truth (the claimed World Cup, Euro,
or Olympic year must exist and match the stadium's country; for World Cups
2006–2018 the venue must also sit within 70 km of a real host city), duplicate
venues within 300 m, placeholder metrics, coordinate junk in name and city
fields, and consistency between the index and the per-stadium files.

That procedure exists because the original event data was scrambled — rows
shifted in the source spreadsheet — and produced false claims on the live site.
The corrections are logged in `DATA_AUDIT.md`.

## Author

**Gustavo Garcia do Amaral**, University of Kansas.

## License

- **Code** (`scripts/`, dashboard source) — MIT, see [`LICENSE`](LICENSE)
- **Data** (`data/`, `Stadia_database.xlsx`) — CC BY 4.0, see [`LICENSE-DATA`](LICENSE-DATA)

## Citation

```bibtex
@misc{amaral_stadia_urban_observatory,
  author       = {Amaral, Gustavo Garcia do},
  title        = {Stadia Urban Observatory: 4-D urban model analysis of
                  major event stadiums},
  year         = {2026},
  howpublished = {\url{https://github.com/UrbanSapiens/stadia-urban-observatory}}
}
```
