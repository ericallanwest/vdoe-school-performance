# Virginia School Performance Map

An interactive web map of every Virginia public school rated under VDOE's **School Performance & Support
Framework** (2025-26 results, released fall 2026). Schools are colored by performance category, shown over
a neighborhood-income layer, with an analysis panel that relates income to performance.

1,811 schools mapped: Distinguished 791 · Meets Expectations 470 · Approaching Expectations 416 ·
Needs Intensive Support 125 · Too Small 9. (VDOE lists 1,812; *Virginia Virtual Academy at Manassas*, rated
Too Small, has no NCES location and is left off.)

Live map: <https://ericallanwest.github.io/vdoe-school-performance/>

## Using the map

- **Filter** by performance category (with live counts), school level, superintendent's region, and division;
  search by name. The Area menu also has the **original study area** — the 47 Southwest & Southside divisions
  this project started with.
- **Click a school** for its framework score and component points (mastery, growth, readiness, graduation),
  federal identification (CSI / TSI / ATSI), and income measures, and to outline its *approximate* attendance zone.
- **Income layers:** census-tract median household income (on by default) and all approximate zones.
- **Analysis panel**, two views:
  - *By category* — a strip chart of every school's income by category, with median and inter-quartile band.
  - *Score vs. income* — framework score against income (log scale), with the approximate category
    cut-offs (65 / 80 / 90) dashed.

  Both report a rank correlation for whatever is currently filtered; switch between three income measures
  (below). Clicking a dot flies the map to that school.

Run locally: `python -m http.server` from the repo root and open <http://localhost:8000>.
It is a static site (Leaflet from cdnjs, Esri gray-canvas basemap), so GitHub Pages serves it as-is.

## Data & method

| Piece | Source |
|---|---|
| Framework score, category, federal ID, components | [VDOE statewide SPSF results, 2025-26](https://www.doe.virginia.gov/teaching-learning-assessment/road-to-readiness-school-performance-and-support-framework-resource-hub/spsf-prior-year-results/fall-2026-statewide-results) (`build/raw/spsf_2025_26.csv`, exported from VDOE's `2025-2026.xlsx`) |
| School locations, grades, enrollment, locale | [NCES EDGE](https://nces.ed.gov/programs/edge/Geographic/SchoolLocations) public school admin data 2024-25 |
| Median household income | ACS 2024 5-year, table B19013, census tracts |
| Households (for weighting) | ACS 2024 5-year, table B11001 |
| Tract, town and locality boundaries | Census TIGER/Line 2024, 2024 Gazetteer |

`build/build_data.py` reproduces everything in `data/` (`pip install geopandas scipy`, then
`python build/build_data.py`; Census downloads are cached in `build/cache/`):

1. **Match** each VDOE school to NCES by division number (NCES `ST_LEAID`) and fuzzy name (≥ 0.9
   similarity). Twelve schools renamed or opened since the NCES snapshot are matched by hand — e.g.
   Charlottesville's 2025 renamings (Johnson → Tall Oaks, Burnley-Moran → Sunrise, Buford → Charlottesville
   Middle), Danville's Arnett Hills (formerly G.L.H. Johnson), Loudoun's Richard and Mildred Loving
   (formerly Frances Hazel Reid); Prince William's new Woodbridge Elementary is placed by geocoding its
   address. See `OVERRIDES` / `MANUAL` in the script; review the joined table in `build/schools_matched.csv`.
2. **Tract income** – the ACS median of the tract containing the school.
3. **Division territory** – each county/city goes to the division of the same name; localities without one go
   to the division that runs their schools (Covington → Alleghany Highlands, Williamsburg + James City →
   W-JCC, Fairfax city → Fairfax County, Emporia → Greensville). The town divisions of Colonial Beach and West
   Point get their town boundary, cut out of the surrounding county.
4. **Approximate attendance zone** – for each division and each grade K–12, the territory is split among the
   schools serving that grade by nearest school (Voronoi); a school's zone is the union of its per-grade cells,
   so primary/intermediate pairs and K-12 schools are handled correctly. Virtual schools get the whole
   division. **Zone income** is the household-weighted average of overlapping tract medians (households
   assumed even within a tract).
5. **Relative to division** – zone income ÷ the median zone income of the division's schools, to separate a
   "richer part of town" effect from a "richer county" effect.

Not used: NCES free/reduced-lunch and direct-certification counts. Virginia reports Community Eligibility
(CEP) schools at ≈100 % of enrollment, so those fields don't measure poverty.

## Findings

Category rank is 1 = Distinguished … 4 = Needs Intensive Support; ρ below is signed so that **positive means
higher income ↔ better performance**. All p-values are < 10⁻¹⁹ statewide (n = 1,802 rated schools).

| Measure | ρ with category | ρ with framework score | Median, Distinguished | Median, Needs Intensive Support |
|---|---|---|---|---|
| Zone income | +0.22 | +0.27 | $104.6k | $70.6k |
| Tract income | +0.21 | +0.26 | $103.3k | $72.8k |
| Relative to division | **+0.32** | **+0.38** | 1.04× | 0.92× |

- **Statewide the relationship is clear but moderate.** Distinguished zones have a median income ~$34k above
  Needs Intensive Support zones, yet every category spans the full income range.
- **The within-division gradient is stronger than the statewide one.** Income relative to the division
  correlates better with performance (ρ = +0.32) than raw income does (+0.22): across Virginia, schools in the
  poorer parts of *their own* division do worse, whatever the division's overall wealth. That is the opposite
  of the original Southwest & Southside study area, where the relative measure showed almost nothing (+0.06).
- **It is a metropolitan pattern.** By region: Central +0.57, Northern Virginia +0.55, Tidewater +0.42,
  Western Virginia +0.27, Valley +0.25 — but Southside +0.12, Southwest +0.03 and Northern Neck −0.07 (not
  significant). The original study area as a whole is +0.13 versus +0.40 for the rest of the state.
- **Strongest within single divisions:** Chesapeake +0.71, Chesterfield +0.68, Prince William +0.65,
  Henrico +0.61, Albemarle +0.60, Fairfax +0.59, Williamsburg-James City +0.57, Roanoke City +0.57. Several
  rural divisions (Montgomery, Washington, Suffolk, Augusta, Pittsylvania) run slightly the other way.
- **By level:** middle +0.30 and elementary +0.28; high schools ≈ 0.
- **Needs Intensive Support** is spread across suburban and urban divisions: Chesterfield 14, Fairfax 14,
  Norfolk 10, Richmond City 9, Roanoke City 9, Henrico 8, Lynchburg 8.

### Caveats

Correlation, not causation: income overlaps with urbanicity, school size, division resources and student need.
Tract ACS estimates carry sizable margins of error (shown in popups), and ACS top-codes tract medians at
$250,000. Zones are geometric approximations, not official boundaries; magnet, choice, alternative and
virtual schools draw from outside them (and, counted as regular schools, shrink their neighbors' Voronoi
cells). Ten schools sit physically outside their own division (e.g. Albemarle's Burley Middle in
Charlottesville); their zones are clipped to their division. Categories are not a pure function of the
score — VDOE applies additional rules, so some high-scoring schools land in lower categories.

## Ideas for next steps

- Swap Voronoi zones for official boundaries where divisions publish them, or NCES SABS.
- Add student-level poverty from VDOE fall membership ("economically disadvantaged %"), which is not distorted
  by CEP.
- Add the 2024-25 (year-one) results to show change between years.
