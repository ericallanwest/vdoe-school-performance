# Virginia School Performance Map

An interactive web map of every Virginia public school rated under VDOE's **School Performance & Support
Framework** (2025-26 results, released fall 2026). Schools are colored by performance category and shown over
a neighborhood-income layer. An analysis panel relates performance to neighborhood income and to student
poverty, and tracks the change from 2024-25.

1,811 schools mapped: Distinguished 791 · Meets Expectations 470 · Approaching Expectations 416 ·
Needs Intensive Support 125 · Too Small 9. (VDOE lists 1,812; *Virginia Virtual Academy at Manassas*, rated
Too Small, has no NCES location and is left off.)

Live map: <https://ericallanwest.github.io/vdoe-school-performance/>

## Using the map

- **Filter** by performance category (with live counts), school level, superintendent's region and division;
  search by name. The Area menu also has the **original study area**: the 47 Southwest & Southside divisions
  this project started with.
- **Click a school** to see:
  - its framework score and component points (mastery, growth, readiness, graduation);
  - its federal identification (CSI / TSI / ATSI);
  - its 2024-25 category and score, and the change since then;
  - its economically disadvantaged share and income measures;
  - its attendance zone outlined on the map: solid if the boundary is published, dashed if approximate.
- **Income layers:** census-tract median household income (on by default) and every visible school's zone.
- **Analysis panel**, three views:
  - *By category*: every school's value by category, with the median and inter-quartile band.
  - *Score*: framework score against the chosen measure, with the approximate category cut-offs
    (65 / 80 / 90) dashed.
  - *Change since 2024-25*: score change against the chosen measure. The table adds a category-to-category
    transition matrix.

  Four measures can be switched: zone income, tract income, zone income relative to the division, and the
  economically disadvantaged share (all described below). Each view reports a rank correlation for whatever
  is currently filtered. Clicking a dot flies the map to that school.

Run locally: `python -m http.server` from the repo root and open <http://localhost:8000>.
It is a static site (Leaflet from cdnjs, Esri gray-canvas basemap), so GitHub Pages serves it as-is.

## Data & method

| Piece | Source |
|---|---|
| Framework score, category, federal ID, components | [VDOE statewide SPSF results](https://www.doe.virginia.gov/teaching-learning-assessment/road-to-readiness-school-performance-and-support-framework-resource-hub/spsf-prior-year-results): 2025-26 (`build/raw/spsf_2025_26.csv`) and 2024-25 (`build/raw/spsf_2024_25.xlsx`) |
| Economically disadvantaged students | [VDOE School Quality Profiles](https://schoolquality.virginia.gov/download-data) assessment download, 2025-26 (`build/raw/sq_econ_disadvantaged_2025_26.csv`, filtered) |
| School locations, grades, enrollment, locale | [NCES EDGE](https://nces.ed.gov/programs/edge/Geographic/SchoolLocations) public school admin data 2024-25 |
| Attendance zones | 42 divisions' own GIS layers (`build/zone_sources.json`); [NCES SABS](https://nces.ed.gov/programs/edge/SABS) 2015-16 |
| Median household income | ACS 2024 5-year, table B19013, census tracts |
| Households (for weighting) | ACS 2024 5-year, table B11001 |
| Tract, town and locality boundaries | Census TIGER/Line 2024, 2024 Gazetteer |

`build/build_data.py` reproduces everything in `data/` (`pip install geopandas scipy openpyxl`, then
`python build/build_data.py`). Census files and zone layers are downloaded once and cached in `build/cache/`.
Delete that folder to pick up boundary changes that divisions have published since.

1. **Match** each VDOE school to NCES by division number (NCES `ST_LEAID`) and fuzzy name (≥ 0.9
   similarity). Twelve schools renamed or opened since the NCES snapshot are matched by hand. Examples:
   - Charlottesville's 2025 renamings (Johnson → Tall Oaks, Burnley-Moran → Sunrise, Buford → Charlottesville
     Middle);
   - Danville's Arnett Hills (formerly G.L.H. Johnson);
   - Loudoun's Richard and Mildred Loving (formerly Frances Hazel Reid).

   Prince William's new Woodbridge Elementary is placed by geocoding its address, and Dominion Trail
   Elementary's NCES coordinates (about 9 km off) are replaced the same way. See `OVERRIDES` / `MANUAL` /
   `LOCATION_FIXES` in the script, and review the joined table in `build/schools_matched.csv`.
2. **2024-25 results** are matched by division and school name. Three schools are new in 2025-26 (Mountain View
   Upper Elementary, Chesterfield's Deep Creek Middle, Gladys West Elementary); three were renamed and are
   linked by hand. VDOE renamed two categories for 2025-26: *On Track* → Meets Expectations and *Off Track* →
   Approaching Expectations. Prior-year categories are shown under the new names.
3. **Economically disadvantaged share.** This is VDOE's student-level flag (direct certification, Medicaid,
   SNAP/TANF, homelessness, migrant or foster status). It is shown as the share of a school's 2025-26 reading
   and math SOL test takers, with the two subjects pooled. It is available for 1,775 schools; the gaps are
   mostly K-2 primary schools, which have no tested grades. Unlike NCES free/reduced-lunch counts, it is not
   inflated at Community Eligibility (CEP) schools. VDOE's fall-membership counts of all enrolled students sit
   behind a CAPTCHA, so tested students stand in for enrollment.
4. **Tract income** – the ACS median of the tract containing the school.
5. **Division territory** – each county or city goes to the division of the same name. Localities without one
   go to the division that runs their schools:
   - Covington → Alleghany Highlands;
   - Williamsburg and James City County → W-JCC;
   - Fairfax city → Fairfax County;
   - Emporia → Greensville.

   The town divisions of Colonial Beach and West Point get their town boundary, cut out of the surrounding
   county.
6. **Attendance zones**, built grade by grade within each division from the best source available:
   1. **Published by the division or locality** (42 divisions, including all the largest except Hampton,
      Suffolk, Frederick, Montgomery and Rockingham). Layer names are matched to schools by name, using
      aliases for shared or campus zones (`build/zone_sources.json`). These are used for a grade when they
      cover at least half the schools serving it.
   2. **NCES School Attendance Boundary Survey 2015-16**, for a grade where SABS's set of schools is exactly
      today's.
   3. **Approximate**: nearest-school (Voronoi) cells of the division's territory.

   A school's zone is the union of its cells across grades. Its label is the weakest source used: published
   1,187 schools · SABS 338 · approximate 244.

   42 schools get the whole division instead of a zone. Most are virtual schools or magnet, charter and
   option schools that publish no zone (TJ, Arlington's option schools, Richmond's specialty schools). The
   rest are zoned schools missing from a layer:
   - Newport News's 2026-27 reorganization (Dutrow, Deer Park, B.T. Washington);
   - Loudoun's Henrietta Lacks Elementary, W.O. Robey High and Watson Mountain Middle;
   - Fairfax's Kings Glen Elementary.

   **Zone income** is the household-weighted average of the tract medians a zone overlaps (households are
   assumed even within a tract).
7. **Relative to division** – zone income ÷ the median zone income of the division's schools, to separate a
   "richer part of town" effect from a "richer county" effect.

## Findings

Category rank is 1 = Distinguished … 4 = Needs Intensive Support. ρ below is signed so that **positive means
better-off ↔ better performance**. All p-values are < 10⁻¹⁹ statewide (n = 1,802 rated schools; 1,769 with a
disadvantaged share).

| Measure | ρ with category | ρ with framework score | Median, Distinguished | Median, Needs Intensive Support |
|---|---|---|---|---|
| Zone income | +0.24 | +0.29 | $105.4k | $70.0k |
| Tract income | +0.21 | +0.26 | $103.3k | $72.8k |
| Relative to division | +0.35 | +0.42 | 1.03× | 0.91× |
| Economically disadvantaged share | **+0.51** | **+0.60** | 32 % | 60 % |

- **Student poverty predicts performance far better than neighborhood income.** The disadvantaged share
  correlates with framework score at ρ = −0.60, against +0.29 for zone income. The two measures overlap
  (ρ = −0.64 between them), but only the student flag captures who actually attends a school.
- **The within-division income gradient is stronger than the statewide one.** Income relative to the division
  correlates better with performance (+0.35) than raw income does (+0.24). Across Virginia, schools in the
  poorer parts of *their own* division do worse, whatever the division's overall wealth. Published zone
  boundaries sharpen this a little compared with the earlier all-approximate zones (+0.32).
- **The income link is mainly metropolitan.** Zone income vs. category by region:
  - Central +0.59, Northern Virginia +0.59, Tidewater +0.46;
  - Western Virginia +0.29, Valley +0.26;
  - Southside +0.09, Southwest +0.03, Northern Neck −0.05 (these three are not significant).

  The original study area is +0.13, against +0.42 for the rest of the state.
- **Student poverty matters everywhere, but less in rural regions.** Disadvantaged share vs. score:
  - Northern Virginia −0.80, Tidewater −0.72, Western Virginia −0.69, Central −0.63, Valley −0.55;
  - Southside −0.48, Southwest −0.30, Northern Neck −0.19.
- **Strongest income gradients within single divisions:** Arlington +0.75, Chesapeake +0.72, Albemarle +0.69,
  Chesterfield +0.68, Prince William +0.68, Fairfax +0.64, Henrico +0.63, Richmond City +0.60. Several rural
  divisions (Washington, Montgomery, Pittsylvania, Suffolk, Augusta) run slightly the other way.
- **By level:** zone income vs. category is +0.32 for middle schools and +0.30 for elementary; ≈ 0 for high
  schools.
- **Year over year (1,798 schools rated in both years):**
  - Scores rose almost everywhere: median +4.4 points, and 88 % of schools improved.
  - Distinguished went from 425 schools statewide to 791; 378 of the 774 matched *On Track* schools moved up.
  - 140 of the 206 Needs Intensive Support schools still rated moved up a category, and 66 stayed.
  - The change barely relates to income or poverty (|ρ| < 0.09).

  A shift this broad suggests that changes to the framework or the tests drove much of it, so compare
  categories across years with care.
- **Needs Intensive Support** is spread across suburban and urban divisions: Chesterfield 14, Fairfax 14,
  Norfolk 10, Richmond City 9, Roanoke City 9, Henrico 8, Lynchburg 8.

### Caveats

- **Correlation, not causation.** Income and poverty overlap with urbanicity, school size, division resources
  and student need.
- **Income estimates are noisy.** Tract ACS estimates carry sizable margins of error (shown in popups), and ACS
  top-codes tract medians at $250,000.
- **Published zones have their own limits:**
  - Some layers are for 2026-27 (Loudoun, Virginia Beach, Henrico, Chesapeake, Newport News).
  - Some are older but still name today's schools (Norfolk 2021-24, Spotsylvania 2020, Alexandria 2014,
    York 2009).
  - A few come from consultant or personal accounts (Culpeper, Page, Shenandoah).
- **SABS zones date from 2015-16.** They are used only where the set of schools is unchanged, but lines may
  have moved since.
- **Approximate zones ignore roads, rivers and policy.** Magnet, choice and virtual schools draw from outside
  any zone.
- **Disadvantaged share covers tested grades only:** grades 3-8 and high-school end-of-course tests.
- **Categories are not a pure function of the score.** VDOE applies additional rules, so some high-scoring
  schools land in lower categories.
