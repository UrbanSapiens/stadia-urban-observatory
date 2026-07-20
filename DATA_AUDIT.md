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

## Fixed on 2026-07-20

**Duplicate venues merged into collection history.** The 32 duplicate pairs
were the same venue collected twice (an old 2020–2024 pass plus the December
2025 pass). They are now merged: the newest becomes the canonical map entry,
and both collections are recorded in a `collections` array on that entry:

```json
"collections":[
  {"slug":"maracan-stadium","date":"2025-12-14","label":"December 2025"},
  {"slug":"maracana-stadium","date":"2020-06-25","label":"June 2020"}
]
```

The map now shows 247 unique venues (was 279). The older collection's data
file (e.g. `data/maracana-stadium.json`) is kept on disk — it is no longer in
`index.json`/the map, but the dashboard fetches it when the user picks that
date. In the dashboard, the stadium-name strip renders one **clickable pill
per collection date**; clicking swaps every panel to that collection's data
(`handleSelectCollection` in index.html). This gives a historical view without
duplicating map pins.

**Events backfilled** for 19 no-event venues where the record is
unambiguous — USA 1994 World Cup venues (Rose Bowl, Stanford, Cotton Bowl,
Soldier Field, Pontiac Silverdome, Giants Stadium, Foxboro, RFK, Camping
World/Citrus Bowl), Olympic stadiums (Rose Bowl 1984, Turner Field/Centennial
1996, Stadio Olimpico Rome 1960 + Italia '90 + Euro 2020, Maracanã 2016),
Stade Vélodrome (WC 1998 + Euro 2016), Estadio Monumental (WC 1978 final),
Parken (Euro 2020), Mercedes-Benz Atlanta (WC 2026 + MLS), and MLB/MLS-only
grounds (SunTrust Park, RingCentral Coliseum, SeatGeek Stadium).

**Spreadsheet synced.** `Stadia_database.xlsx` event columns now match the
corrected JSON for all rows (0 diffs), and America First Field's name/city/
capacity/opening year are filled.

## Open findings (need a decision, not auto-fixable)

- **8 stadiums still with no event recorded** — left for manual review because
  I could not confirm a taxonomy event: Aviva (Dublin) and San Mamés (Bilbao)
  were both *dropped* as Euro 2020 hosts; La Bombonera, Estadio Presidente
  Perón, and four Goiânia grounds (Serra Dourada, Antônio Accioly, Pedro
  Ludovico, Serrinha) are club stadiums that hosted no event in the taxonomy.
  List: `python3 scripts/audit_data.py | grep "no event"`.
- **36 stadiums with placeholder builtArea** (36.0 / 0 / −1) — recompute or
  leave hidden. Currently hidden in the UI. No solution yet.
- **2 duplicate rows in the spreadsheet** (`seatgeek-stadium`,
  `camping-world-stadium`) — same venue under two `master_path` values
  ("MLS Stadium/…" and "United States/…"). Left in place; delete one of each
  if you want the master deduplicated.
- `senegal-national-wrestling-arena` has Riyadh coordinate junk in its city
  field (bad scrape).
