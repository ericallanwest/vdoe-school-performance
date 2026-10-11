"""Build the data files behind the school performance web map.

Inputs
  raw/performance_categories.csv   VDOE performance categories (division, school, category)
  raw/nces_va_schools_2425.json    NCES EDGE public school locations/admin data, 2024-25, Virginia
  Census TIGER 2024 tracts + ACS 2024 5-year tables B19013 (median household income) and
  B11001 (households), downloaded to cache/

Outputs (paths relative to the repo root, which is the static site)
  data/schools.geojson      one point per school with category, NCES attributes, tract income
  data/tracts.geojson       simplified tracts for the study area with median household income
  data/zones.geojson        approximate attendance zones (per-grade Voronoi within each division)
  data/summary.json         income / poverty statistics by performance category
  build/schools_matched.csv flat table for review in a spreadsheet

Run:  pip install geopandas scipy && python3 build/build_data.py
"""

import csv
import difflib
import json
import re
import statistics
import urllib.request
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import shapely
from scipy import stats

BUILD = Path(__file__).resolve().parent
RAW = BUILD / "raw"
CACHE = BUILD / "cache"
OUT = BUILD.parent / "data"

TRACTS_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_51_tract.zip"
ACS_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
ACS_INCOME_URL = ACS_BASE + "acsdt5y2024-b19013.dat"
ACS_HOUSEHOLDS_URL = ACS_BASE + "acsdt5y2024-b11001.dat"
EQUAL_AREA = "EPSG:5070"  # CONUS Albers
GRADES = ["KG"] + [f"{g:02d}" for g in range(1, 13)]

CATEGORIES = ["Distinguished", "Meets Expectations", "Approaching Expectations",
              "Needs Intensive Support", "Too Small", "Not Rated"]
RANK = {c: i + 1 for i, c in enumerate(CATEGORIES[:4])}  # 1 = best, 4 = worst

# Schools whose VDOE name doesn't match NCES 2024-25 (renamed since NCES snapshot).
OVERRIDES = {
    ("danville city", "arnett hills elementary"): "510111000389",  # formerly G.L.H. Johnson Elementary
}


def fetch(url):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, path)
    return path


def norm(name):
    s = name.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(school|the)\b", " ", s).replace("senior high", "high")
    return " ".join(s.split())


def level_of(name, nces_level):
    n = name.lower()
    if "virtual" in n:
        return "Virtual"
    if nces_level in ("Elementary", "Middle", "High"):
        return nces_level
    if "/middle" in n or "combined" in n or "k-12" in n:
        return "Combined"
    if "high" in n:
        return "High"
    if "middle" in n:
        return "Middle"
    return "Elementary" if ("elementary" in n or "primary" in n) else "Other"


def clean(v):
    return None if v is None or (isinstance(v, (int, float)) and v < 0) else v


def match_schools():
    nces = json.load(open(RAW / "nces_va_schools_2425.json"))
    by_id = {r["NCESSCH"]: r for r in nces}
    by_div = defaultdict(list)
    for r in nces:
        by_div[r["LEA_NAME"].replace(" Public Schools", "").strip().lower()].append(r)

    out = []
    with open(RAW / "performance_categories.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            div = row["School division"].strip()
            name = row["School Name"].strip()
            cat = row["Performance Category"].strip() or "Not Rated"
            key = (div.lower(), name.lower())
            if key in OVERRIDES:
                rec, score = by_id[OVERRIDES[key]], 1.0
            else:
                cands = by_div[div.lower()]
                target = norm(name)
                rec = max(cands, key=lambda c: difflib.SequenceMatcher(None, target, norm(c["SCH_NAME"])).ratio())
                score = difflib.SequenceMatcher(None, target, norm(rec["SCH_NAME"])).ratio()
                if score < 0.9:
                    raise SystemExit(f"weak match {div} / {name} -> {rec['SCH_NAME']} ({score:.2f}); add an override")
            enroll = clean(rec["MEMBER"])
            out.append({
                "division": div,
                "name": name,
                "category": cat,
                "rank": RANK.get(cat),
                "level": level_of(name, rec["SCHOOL_LEVEL"]),
                "ncessch": rec["NCESSCH"],
                "nces_name": rec["SCH_NAME"],
                "address": f'{rec["LSTREET1"]}, {rec["LCITY"]} {rec["LZIP"]}',
                "grades": f'{rec["GSLO"]}-{rec["GSHI"]}',
                "_gslo": rec["GSLO"], "_gshi": rec["GSHI"],
                "locale": (rec["ULOCALE"] or "").split("-", 1)[-1],
                "enrollment": enroll,
                # NCES free/reduced lunch and direct-certification counts are not used: Virginia reports
                # Community Eligibility (CEP) schools at ~100% of enrollment, which most of this region is.
                "lat": rec["LATCOD"],
                "lon": rec["LONCOD"],
            })
    return out


def load_acs(url):
    """Return {tract GEOID: (estimate, moe)} for Virginia tracts from an ACS table-based summary file.
    Negative values are Census jam codes (suppressed / not applicable) and become None."""
    out = {}
    with open(fetch(url)) as f:
        next(f)
        for line in f:
            geo, est, moe = line.rstrip("\n").split("|")[:3]
            if geo.startswith("1400000US51"):
                pos = lambda v: int(v) if v.lstrip("-").isdigit() and int(v) > 0 else None
                out[geo[9:]] = (pos(est), pos(moe))
    return out


def grades_served(s):
    """Grades (KG..12) a school serves; PK counts as KG. None for virtual/unknown spans."""
    lo, hi = ("KG" if g == "PK" else g for g in (s["_gslo"], s["_gshi"]))
    if s["level"] == "Virtual" or lo not in GRADES or hi not in GRADES:
        return None
    return GRADES[GRADES.index(lo):GRADES.index(hi) + 1]


def build_zones(schools, tracts):
    """Approximate attendance zones: for each division and grade, split the division's territory
    among the schools serving that grade by nearest school (Voronoi); a school's zone is the union
    of its per-grade cells. Virtual schools (and unknown spans) get the whole division."""
    pts = gpd.GeoSeries(gpd.points_from_xy([s["lon"] for s in schools], [s["lat"] for s in schools]),
                        crs="EPSG:4269").to_crs(EQUAL_AREA)
    county_shapes = tracts.dissolve("COUNTYFP").geometry
    by_div = defaultdict(list)
    for i, s in enumerate(schools):
        by_div[s["division"]].append(i)

    zones = [None] * len(schools)
    for div, idx in by_div.items():
        territory = shapely.union_all([county_shapes[schools[i]["tract"][2:5]] for i in idx])
        cells = defaultdict(list)
        for g in GRADES:
            serving = [i for i in idx if g in (grades_served(schools[i]) or [])]
            if not serving:
                continue
            if len(serving) == 1:
                cells[serving[0]].append(territory)
                continue
            vor = shapely.voronoi_polygons(shapely.MultiPoint([pts[i] for i in serving]), extend_to=territory)
            for i in serving:
                cell = next(c for c in vor.geoms if c.contains(pts[i]))
                cells[i].append(cell.intersection(territory))
        for i in idx:
            zones[i] = shapely.union_all(cells[i]) if cells[i] else territory
    return gpd.GeoSeries(zones, crs=EQUAL_AREA)


def zone_income(zones, tracts):
    """Household-weighted mean of tract median incomes over each zone, assuming households are spread
    evenly within a tract (areal interpolation)."""
    t = tracts.dropna(subset=["income", "households"])
    t = t[t["households"] > 0]
    sindex = t.sindex
    result = []
    for z in zones:
        num = den = 0.0
        for j in sindex.query(z, predicate="intersects"):
            row = t.iloc[j]
            share = row.geometry.intersection(z).area / row.geometry.area
            w = row["households"] * share
            num += w * row["income"]
            den += w
        result.append(round(num / den) if den else None)
    return result


def main():
    schools = match_schools()
    income = load_acs(ACS_INCOME_URL)
    households = load_acs(ACS_HOUSEHOLDS_URL)

    tracts = gpd.read_file(fetch(TRACTS_URL))[["GEOID", "NAMELSAD", "COUNTYFP", "geometry"]]
    tracts["income"] = tracts["GEOID"].map(lambda g: income.get(g, (None, None))[0])
    tracts["moe"] = tracts["GEOID"].map(lambda g: income.get(g, (None, None))[1])
    tracts["households"] = tracts["GEOID"].map(lambda g: households.get(g, (None, None))[0])
    pts = gpd.GeoDataFrame(schools, geometry=gpd.points_from_xy([s["lon"] for s in schools],
                                                                [s["lat"] for s in schools]), crs="EPSG:4269")
    joined = gpd.sjoin(pts, tracts.to_crs("EPSG:4269"), how="left", predicate="within")
    for s, geoid in zip(schools, joined["GEOID"]):
        s["tract"] = geoid
        s["tract_income"], s["tract_income_moe"] = income.get(geoid, (None, None))

    tracts_ea = tracts.to_crs(EQUAL_AREA)
    zones = build_zones(schools, tracts_ea)
    for s, inc in zip(schools, zone_income(zones, tracts_ea)):
        s["zone_income"] = inc
    zone_gdf = gpd.GeoDataFrame({"ncessch": [s["ncessch"] for s in schools]}, geometry=zones)
    zone_gdf["geometry"] = zone_gdf.geometry.simplify(100)
    for s in schools:
        del s["_gslo"], s["_gshi"]

    # Study-area tracts: every tract in a county/city that contains a mapped school.
    counties = {s["tract"][2:5] for s in schools if s["tract"]}
    area = tracts_ea[tracts_ea["COUNTYFP"].isin(counties)].copy()
    area["geometry"] = area.geometry.simplify(60)  # metres (Albers)
    area = area.to_crs("EPSG:4326")

    # Income relative to the division: zone income / median zone income of the division's schools.
    # Separates "richer part of town" from "richer county" effects.
    div_med = {d: statistics.median([s["zone_income"] for s in schools if s["division"] == d and s["zone_income"]])
               for d in {s["division"] for s in schools}}
    for s in schools:
        s["zone_income_vs_division"] = round(s["zone_income"] / div_med[s["division"]], 3) if s["zone_income"] else None

    # Summary statistics by category, for each income measure.
    measures = {"tract_income": "Income of the tract containing the school",
                "zone_income": "Household-weighted income of the approximate attendance zone",
                "zone_income_vs_division": "Zone income relative to the division median"}
    summary = {"n_schools": len(schools), "measures": measures, "by_category": {}, "tests": {}}
    for cat in CATEGORIES:
        grp = [s for s in schools if s["category"] == cat]
        entry = {"n": len(grp)}
        for m in measures:
            xs = sorted(s[m] for s in grp if s[m] is not None)
            entry[m] = {
                "median": xs and statistics.median(xs),
                "quartiles": statistics.quantiles(xs, n=4) if len(xs) >= 4 else None,
                "min": xs[0] if xs else None, "max": xs[-1] if xs else None,
            }
        summary["by_category"][cat] = entry
    rated = [s for s in schools if s["rank"]]
    for m in measures:
        xs = [s for s in rated if s[m] is not None]
        rho, p = stats.spearmanr([s[m] for s in xs], [s["rank"] for s in xs])
        h, kp = stats.kruskal(*[[s[m] for s in xs if s["rank"] == r] for r in RANK.values()])
        summary["tests"][m] = {"spearman_rho": round(rho, 3), "spearman_p": float(f"{p:.2g}"),
                               "kruskal_H": round(h, 2), "kruskal_p": float(f"{kp:.2g}"), "n": len(xs)}

    OUT.mkdir(parents=True, exist_ok=True)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(s["lon"], 6), round(s["lat"], 6)]},
        "properties": {k: v for k, v in s.items() if k not in ("lat", "lon")},
    } for s in schools]
    json.dump({"type": "FeatureCollection", "features": features},
              open(OUT / "schools.geojson", "w"), separators=(",", ":"))
    area[["GEOID", "NAMELSAD", "income", "moe", "geometry"]].to_file(
        OUT / "tracts.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    zone_gdf.to_crs("EPSG:4326").to_file(OUT / "zones.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    json.dump(summary, open(OUT / "summary.json", "w"), indent=1, default=lambda v: round(v, 3))

    with open(BUILD / "schools_matched.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(schools[0].keys()))
        w.writeheader()
        w.writerows(schools)

    print(f"{len(schools)} schools, {len(area)} tracts in {len(counties)} localities")
    for m, t in summary["tests"].items():
        print(m, t)
    for cat, e in summary["by_category"].items():
        print(f"{cat:26} n={e['n']:3}", *(f"{m}={e[m]['median']}" for m in measures))

if __name__ == "__main__":
    main()
