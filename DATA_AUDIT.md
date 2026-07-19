# Data accuracy procedure

The dashboard shows interpretative text and "Hosted Events" tags generated
from the data files. Anything wrong in the data becomes a false statement on
the public site, so **every data collection must pass the audit before it is
pushed**.

## The procedure

1. **Edit / regenerate** the per-stadium files `data/<slug>.json`. The `m`
   block of each stadium file is the single source of truth for identity
   fields (name, city, country, capacity, openingYear, primarySport, events).
2. **Audit:** `python3 scripts/audit_data.py`
   Fix every `ERROR` (false event claims, coordinate names, index/file
   mismatches). Review `WARN`/`REVIEW` items. The script exits non-zero while
   ERRORs remain.
3. **Sync:** `python3 scripts/sync_index.py`
   Propagates the stadium files into `data/index.json` **and** the `IX` array
   embedded in `index.html`. The live site does *not* fetch
   `data/index.json` — if you skip this step the site keeps showing old data.
4. **Re-audit** (should print 0 ERROR), then commit `data/` + `index.html`
   together and push.

## What the audit checks

- Event claims vs ground truth: the claimed World Cup / Euro / Olympics year
  must exist and match the stadium's country; for WC 2006–2018 the stadium
  must also be within 70 km of an actual host city.
- Duplicate venues (two entries within 300 m).
- Placeholder metrics (builtArea outside (0,1], e.g. 36.0 / 0 / −1).
- Names that are raw coordinates; city fields containing coordinate junk or a
  repeated country name.
- Consistency between `data/index.json` and each `data/<slug>.json`.

## Fixed on 2026-07-19

The original event data was scrambled (rows shifted in the source
spreadsheet), producing false claims such as Subaru Park "FIFA WC 2010"
(2010 is its *opening year*; South Africa hosted WC 2010) and Amaan Stadium
(Zanzibar) tagged with Olympics 2000 + WC 2014 + MLS. Corrected:

| Stadium | Was | Now |
|---|---|---|
| Subaru Park | FIFA WC 2010 + MLS | MLS |
| Fritz-Walter-Stadion | FIFA WC 2010 | FIFA WC 2006 |
| National Stadium (Warsaw) | FIFA WC 2010 + Euro 2012 | Euro 2012 |
| Baku Olympic Stadium | WC 2018 + Euro 2020 + MLS | Euro 2020 |
| Amaan Stadium | Olympics 2000 + WC 2014 + MLS + CSD | China Stadium Diplomacy |
| Benjamin Mkapa Stadium | Olympics 2008 + WC 2002 + CSD | China Stadium Diplomacy |
| Safaricom Stadium (Kasarani) | WC 2002 + CSD | China Stadium Diplomacy |
| Sydney Football Stadium | Olympics 2000 + WC 2022 | Olympics 2000 |
| Serra Dourada, Pedro Ludovico, Serrinha, Antônio Accioly (all Goiânia) | FIFA WC 2014 | none — Goiânia hosted no WC 2014 matches |
| `405827983-1118959807` | name was raw coordinates | America First Field, Sandy, Utah |

UI: removed city/coordinates from the dashboard header (kept only the
interpretative headline; location facts live in the Stadium Profile panel),
fixed untruthful percentile labels ("bottom 25%" was shown for the 25–50th
percentile — now "bottom half"), removed the false "within 3km" claim,
hid the Built Area Ratio row when the value is a placeholder.

## Open findings (need a decision, not auto-fixable)

- **32 duplicate venue pairs** — same venue under two slugs, usually an old
  collection (2020–2024) plus the December 2025 one (e.g. `maracan-stadium`
  2025 vs `maracana-stadium` 2020; `subaru-park-stadium` vs
  `talen-energy-stadium`; `stadio-olimpico-rome` vs `stadio-olimpicorome`).
  Both currently appear on the map. Decide: keep only the newest, or model
  them as one venue with multiple collections. Full list:
  `python3 scripts/audit_data.py | grep "same venue"`.
- **57 stadiums with no event recorded** (e.g. Maracanã, Rose Bowl, Foxboro —
  all clearly tournament venues). They render gray on the map and show no
  Hosted Events. Backfill from the study spreadsheet.
- **36 stadiums with placeholder builtArea** (36.0 / 0 / −1) — recompute or
  leave hidden.
- `senegal-national-wrestling-arena` has Riyadh coordinate junk in its city
  field (bad scrape).
