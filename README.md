# School Performance Map — Southwest & Southside Virginia

An interactive web map of the 415 schools in VDOE Superintendent's Regions 6 & 7, colored by their
**School Performance & Support Framework** category, with a neighborhood-income layer and an
analysis panel that relates income to performance.

Distinguished 217 · Meets Expectations 110 · Approaching Expectations 61 · Needs Intensive Support 23 ·
Too Small 3 · not rated 1

## Using the map

- **Filter** by performance category (with live counts), school level, and division; search by name.
- **Click a school** for its details and to outline its *approximate* attendance zone.
- **Income layers:** census-tract median household income (on by default) and all approximate zones.
- **Analysis panel:** a strip chart of every school's income by category, with median and inter-quartile
  band, a rank correlation for whatever is currently filtered, and a table view. Switch between three income
  measures (below). Clicking a dot flies the map to that school.

Live map: <https://ericallanwest.github.io/vdoe-school-performance/>

Run locally: `python3 -m http.server` from the repo root and open <http://localhost:8000>.
It is a static site (Leaflet from cdnjs, Esri gray-canvas basemap), so GitHub Pages can serve it as-is.

## Data & method

| Piece | Source |
|---|---|
| Performance category | Supplied CSV (`build/raw/performance_categories.csv`) |
| School locations, grades, enrollment, locale | [NCES EDGE](https://nces.ed.gov/programs/edge/Geographic/SchoolLocations) public school admin data 2024-25 |
| Median household income | ACS 2024 5-year, table B19013, census tracts |
| Households (for weighting) | ACS 2024 5-year, table B11001 |
| Tract boundaries | Census TIGER/Line 2024 |

`build/build_data.py` reproduces everything in `data/`:

1. **Match** each school to NCES by division + fuzzy name (all 415 matched at ≥ 0.9 similarity; one manual
   override: *Arnett Hills Elementary* = NCES *G.L.H. Johnson Elementary*, Danville). Review the joined
   table in `build/schools_matched.csv`.
2. **Tract income** – the ACS median of the tract containing the school.
3. **Approximate attendance zone** – for each division and each grade K–12, the division's territory is split
   among the schools serving that grade by nearest school (Voronoi); a school's zone is the union of its
   per-grade cells, so primary/intermediate pairs and K-12 schools are handled correctly. Virtual schools get
   the whole division. **Zone income** is the household-weighted average of overlapping tract medians
   (households assumed even within a tract).
4. **Relative to division** – zone income ÷ the median zone income of the division's schools, to separate a
   "richer part of town" effect from a "richer county" effect.

Not used: NCES free/reduced-lunch and direct-certification counts. Virginia reports Community Eligibility
(CEP) schools at ≈100 % of enrollment — e.g. Roanoke's Crystal Spring Elementary, in a $98k tract, shows 99 % —
and most divisions in this region are fully CEP, so those fields don't measure poverty here.

## Findings

Category rank is 1 = Distinguished … 4 = Needs Intensive Support; ρ below is signed so that **positive means
higher income ↔ better category**.

| Measure | Spearman ρ | p | Median, Distinguished | Median, Needs Intensive Support |
|---|---|---|---|---|
| Zone income | **+0.13** | 0.008 | $58.2k | $50.6k |
| Tract income | +0.10 | 0.046 | $56.8k | $49.5k |
| Relative to division | +0.06 | 0.20 | 1.00× | 0.94× |

- **Overall the relationship is real but weak.** Income explains little of the category spread; plenty of
  Distinguished schools sit in $40k zones, and Meets Expectations schools have slightly *higher* median income
  than Distinguished ones.
- **Needs Intensive Support is concentrated in the cities.** 20 of 23 are in Roanoke City (9), Lynchburg (8)
  and Danville (3); these zones are the lowest-income group (median ≈ $51k).
- **Inside some divisions the gradient is strong.** Roanoke City ρ = +0.57 (p = 0.004), Bedford County +0.51
  (p = 0.03), Lynchburg +0.49 (p = 0.07); others (Montgomery, Tazewell, Washington) run slightly the other way.
  Use the Division filter to see each.
- **By level:** elementary ρ = +0.18 (p = 0.005); middle +0.17 (n.s.); high schools ≈ 0.
- Once income is expressed *relative to the division*, the overall effect mostly disappears (ρ = +0.06,
  n.s.) — much of the regional pattern is between divisions (urban vs. rural), not within them.

### Caveats

Correlation, not causation: income overlaps with rurality, school size, division resources and student need.
Tract ACS estimates carry sizable margins of error (shown in popups). Zones are geometric approximations,
not official boundaries; magnet, choice and virtual schools draw from outside them.

## Ideas for next steps

- Swap Voronoi zones for official boundaries where divisions publish them (Roanoke City, Lynchburg, etc.) or
  NCES SABS.
- Add student-level poverty from VDOE fall membership ("economically disadvantaged %"), which is not distorted
  by CEP.
- Extend statewide once all regions' categories are available.
