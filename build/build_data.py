"""Build the data files behind the school performance web map.

Inputs
  raw/spsf_2025_26.csv             VDOE School Performance & Support Framework, 2025-26 results (released fall
                                   2026), statewide: division/school numbers, framework score, category,
                                   federal identification, component scores
  raw/nces_va_schools_2425.json    NCES EDGE public school locations/admin data, 2024-25, Virginia
  Census TIGER 2024 tracts and places, 2024 Gazetteer counties, ACS 2024 5-year tables B19013 (median
  household income) and B11001 (households), downloaded to cache/

Outputs (paths relative to the repo root, which is the static site)
  data/schools.geojson      one point per school with category, score, NCES attributes, income measures
  data/tracts.geojson       simplified Virginia tracts with median household income
  data/zones.geojson        approximate attendance zones (per-grade Voronoi within each division)
  data/summary.json         income statistics by performance category and correlation tests
  build/schools_matched.csv flat table for review in a spreadsheet

Run:  pip install geopandas scipy && python build/build_data.py
"""

import csv
import difflib
import io
import json
import re
import statistics
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import shapely
from scipy import stats

BUILD = Path(__file__).resolve().parent
RAW = BUILD / "raw"
CACHE = BUILD / "cache"
OUT = BUILD.parent / "data"

TRACTS_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_51_tract.zip"
PLACES_URL = "https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_51_place.zip"
COUNTIES_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip"
ACS_BASE = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/"
ACS_INCOME_URL = ACS_BASE + "acsdt5y2024-b19013.dat"
ACS_HOUSEHOLDS_URL = ACS_BASE + "acsdt5y2024-b11001.dat"
EQUAL_AREA = "EPSG:5070"  # CONUS Albers
GRADES = ["KG"] + [f"{g:02d}" for g in range(1, 13)]

CATEGORIES = ["Distinguished", "Meets Expectations", "Approaching Expectations",
              "Needs Intensive Support", "Too Small", "Not Rated"]
RANK = {c: i + 1 for i, c in enumerate(CATEGORIES[:4])}  # 1 = best, 4 = worst
LEVELS = {"ES": "Elementary", "MS": "Middle", "HS": "High"}  # VDOE model types; combinations -> Combined
COMPONENTS = ["Mastery", "Growth", "Readiness", "Graduation"]

# VDOE school names that don't match the NCES 2024-25 snapshot (renamed or opened since).
OVERRIDES = {
    ("Danville City", "Arnett Hills Elementary"): "510111000389",  # formerly G.L.H. Johnson Elementary
    ("Charlottesville City", "Tall Oaks Elementary"): "510078000266",  # formerly Johnson Elementary (2025)
    ("Charlottesville City", "Sunrise Elementary"): "510078000272",  # formerly Burnley-Moran Elementary (2025)
    ("Charlottesville City", "Charlottesville Middle"): "510078000271",  # formerly Buford Middle (2025)
    ("Loudoun County", "Richard and Mildred Loving Elementary"): "510225002412",  # formerly Frances Hazel Reid
    ("Loudoun County", "Gum Spring Middle"): "510225002450",  # formerly Mercer Middle
    ("Chesapeake City", "B.M. Williams Elementary"): "510081000280",  # formerly B.M. Williams Primary
    ("Chesapeake City", "Crestwood Elementary School"): "510081000286",  # formerly Crestwood Intermediate
    ("Fredericksburg City", "Gladys West Elementary"): "510151003155",  # NCES "Fredericksburg New Elementary"
    ("Albemarle County", "Mountain View Upper Elementary"): "510009002173",  # Mountain View Elementary site
    ("Richmond City", "Richmond Success Academy"): "510324002307",  # assumed: Richmond Alternative School site
}
GRADE_OVERRIDES = {"510151003155": ("KG", "05")}  # NCES lists the new school without grades
# Schools with no NCES 2024-25 record at all: location from the Census geocoder.
MANUAL = {
    ("Prince William County", "Woodbridge Elementary"): {
        "SCH_NAME": "(not in NCES 2024-25)", "LSTREET1": "1550 Prince William Pkwy", "LCITY": "Woodbridge",
        "LZIP": "22191", "GSLO": "KG", "GSHI": "05", "ULOCALE": "21-Suburb: Large", "MEMBER": None,
        "LATCOD": 38.650193, "LONCOD": -77.261943,
    },
}
UNMAPPED = {("Manassas City", "Virginia Virtual Academy at Manassas")}  # no NCES record; rated Too Small

# Divisions that are towns inside a county rather than whole localities: territory = the town boundary.
TOWN_DIVISIONS = {"Colonial Beach": "Colonial Beach town", "West Point": "West Point town"}

REGIONS = {1: "Central", 2: "Tidewater", 3: "Northern Neck", 4: "Northern Virginia",
           5: "Valley", 6: "Western Virginia", 7: "Southwest", 8: "Southside"}
# The 47 divisions in the regional file this map started from (Regions 6 & 7 plus parts of 5 & 8).
ORIGINAL_STUDY_AREA = {
    "Alleghany Highlands", "Amherst County", "Appomattox County", "Bath County", "Bedford County",
    "Bland County", "Botetourt County", "Bristol City", "Buchanan County", "Buena Vista City",
    "Campbell County", "Carroll County", "Charlotte County", "Craig County", "Danville City",
    "Dickenson County", "Floyd County", "Franklin County", "Galax City", "Giles County", "Grayson County",
    "Halifax County", "Henry County", "Highland County", "Lee County", "Lexington City", "Lynchburg City",
    "Martinsville City", "Mecklenburg County", "Montgomery County", "Norton City", "Patrick County",
    "Pittsylvania County", "Prince Edward County", "Pulaski County", "Radford City", "Roanoke City",
    "Roanoke County", "Rockbridge County", "Russell County", "Salem City", "Scott County", "Smyth County",
    "Tazewell County", "Washington County", "Wise County", "Wythe County",
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


def num(v):
    """VDOE cells: numbers, '^' (group too small) or '-' (not applicable)."""
    try:
        return float(v)
    except ValueError:
        return None


def clean(v):
    return None if v is None or (isinstance(v, (int, float)) and v < 0) else v


def load_spsf():
    with open(RAW / "spsf_2025_26.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    start = next(i for i, r in enumerate(rows) if r and r[0].startswith("Superintendent")) + 1
    out = []
    for r in rows[start:]:
        if len(r) < 17 or not r[4].strip():
            continue
        comps = {}
        for k, c in enumerate(COMPONENTS):
            earned, possible = num(r[9 + 2 * k]), num(r[10 + 2 * k])
            if possible:
                comps[c.lower()] = [earned, possible]
        out.append({
            "region": int(r[0]), "div_num": int(r[1]), "division": r[2].strip(),
            "school_num": int(r[3]), "name": r[4].strip(), "model": r[5].strip(),
            "score": num(r[6]), "category": r[7].strip() or "Not Rated",
            "federal_id": r[8].strip() or None, "components": comps,
        })
    return out


def level_of(s):
    if "virtual" in s["name"].lower():
        return "Virtual"
    return LEVELS.get(s["model"], "Combined")


def match_schools():
    nces = json.load(open(RAW / "nces_va_schools_2425.json"))
    by_id = {r["NCESSCH"]: r for r in nces}
    by_div = defaultdict(list)
    for r in nces:
        if r["ST_LEAID"].startswith("VA-"):
            by_div[int(r["ST_LEAID"][3:])].append(r)

    out, skipped = [], []
    for s in load_spsf():
        key = (s["division"], s["name"])
        if key in UNMAPPED:
            skipped.append(s)
            continue
        if key in MANUAL:
            rec = {**MANUAL[key], "NCESSCH": f"VDOE-{s['div_num']:03d}-{s['school_num']:04d}"}
        elif key in OVERRIDES:
            rec = by_id[OVERRIDES[key]]
        else:
            target = norm(s["name"])
            rec = max(by_div[s["div_num"]],
                      key=lambda c: difflib.SequenceMatcher(None, target, norm(c["SCH_NAME"])).ratio())
            score = difflib.SequenceMatcher(None, target, norm(rec["SCH_NAME"])).ratio()
            if score < 0.9:
                raise SystemExit(f"weak match {key} -> {rec['SCH_NAME']} ({score:.2f}); add an override")
        gslo, gshi = GRADE_OVERRIDES.get(rec["NCESSCH"], (rec["GSLO"], rec["GSHI"]))
        out.append({
            **s,
            "region_name": REGIONS[s["region"]],
            "study_area": s["division"] in ORIGINAL_STUDY_AREA,
            "rank": RANK.get(s["category"]),
            "level": level_of(s),
            "ncessch": rec["NCESSCH"],
            "nces_name": rec["SCH_NAME"],
            "address": f'{rec["LSTREET1"]}, {rec["LCITY"]} {rec["LZIP"]}',
            "grades": f"{gslo}-{gshi}",
            "_gslo": gslo, "_gshi": gshi,
            "locale": (rec["ULOCALE"] or "").split("-", 1)[-1],
            "enrollment": clean(rec["MEMBER"]),
            # NCES free/reduced lunch and direct-certification counts are not used: Virginia reports
            # Community Eligibility (CEP) schools at ~100% of enrollment.
            "lat": rec["LATCOD"],
            "lon": rec["LONCOD"],
        })
    dupes = [k for k, n in Counter(s["ncessch"] for s in out).items() if n > 1]
    if dupes:
        raise SystemExit(f"several VDOE schools matched the same NCES school: {dupes}")
    return out, skipped


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


def locality_names():
    """{county FIPS (3 digits): name} for Virginia counties and independent cities."""
    with zipfile.ZipFile(fetch(COUNTIES_URL)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8")
    rows = csv.DictReader(io.StringIO(text), delimiter="\t")
    return {r["GEOID"][2:]: r["NAME"] for r in rows if r["USPS"] == "VA"}


def division_territories(schools, tracts):
    """Each division's territory in EQUAL_AREA. Localities go to the division with the same name; the rest
    (e.g. Covington, Fairfax city, Williamsburg) to the division with most schools there, else the nearest
    school's division. Town divisions get their town boundary, cut out of the surrounding county's."""
    localities = tracts.dissolve("COUNTYFP").geometry
    names = locality_names()
    divisions = {s["division"] for s in schools}
    by_name = {d.lower(): d for d in divisions}
    schools_in = defaultdict(Counter)
    for s in schools:
        schools_in[s["tract"][2:5]][s["division"]] += 1
    pts = gpd.GeoSeries(gpd.points_from_xy([s["lon"] for s in schools], [s["lat"] for s in schools]),
                        crs="EPSG:4269").to_crs(EQUAL_AREA)

    owner = {}
    for fips, geom in localities.items():
        name = names[fips].lower()
        if name in by_name:
            owner[fips] = by_name[name]
        elif schools_in[fips]:
            owner[fips] = schools_in[fips].most_common(1)[0][0]
        else:
            owner[fips] = schools[int(pts.distance(geom.centroid).idxmin())]["division"]
    unnamed = sorted(f"{names[f]} -> {d}" for f, d in owner.items() if names[f].lower() not in by_name)
    print("localities assigned without a same-name division:", "; ".join(unnamed))

    territory = {d: shapely.union_all([localities[f] for f, o in owner.items() if o == d])
                 for d in set(owner.values())}
    places = gpd.read_file(fetch(PLACES_URL)).to_crs(EQUAL_AREA).set_index("NAMELSAD").geometry
    for div, place in TOWN_DIVISIONS.items():
        town = places[place]
        for d in territory:
            if territory[d].intersects(town):
                territory[d] = territory[d].difference(town)
        territory[div] = town
    missing = divisions - territory.keys()
    if missing:
        raise SystemExit(f"divisions without territory: {missing}")
    return territory


def grades_served(s):
    """Grades (KG..12) a school serves; PK counts as KG. None for virtual/unknown spans."""
    lo, hi = ("KG" if g == "PK" else g for g in (s["_gslo"], s["_gshi"]))
    if s["level"] == "Virtual" or lo not in GRADES or hi not in GRADES:
        return None
    return GRADES[GRADES.index(lo):GRADES.index(hi) + 1]


def build_zones(schools, territory):
    """Approximate attendance zones: for each division and grade, split the division's territory
    among the schools serving that grade by nearest school (Voronoi); a school's zone is the union
    of its per-grade cells. Virtual schools (and unknown spans) get the whole division."""
    pts = gpd.GeoSeries(gpd.points_from_xy([s["lon"] for s in schools], [s["lat"] for s in schools]),
                        crs="EPSG:4269").to_crs(EQUAL_AREA)
    by_div = defaultdict(list)
    for i, s in enumerate(schools):
        by_div[s["division"]].append(i)

    zones = [None] * len(schools)
    for div, idx in by_div.items():
        terr = territory[div]
        cells = defaultdict(list)
        for g in GRADES:
            serving = [i for i in idx if g in (grades_served(schools[i]) or [])]
            if not serving:
                continue
            if len(serving) == 1:
                cells[serving[0]].append(terr)
                continue
            vor = shapely.voronoi_polygons(shapely.MultiPoint([pts[i] for i in serving]), extend_to=terr)
            for i in serving:
                cell = min(vor.geoms, key=lambda c: c.distance(pts[i]))  # duplicate points share a cell
                cells[i].append(cell.intersection(terr))
        for i in idx:
            zones[i] = shapely.union_all(cells[i]) if cells[i] else terr
    outside = [schools[i]["name"] for i in range(len(schools)) if not zones[i].buffer(1).contains(pts[i])]
    print(f"{len(outside)} schools sit outside their division's territory (zone kept):", "; ".join(outside[:12]))
    return gpd.GeoSeries(zones, crs=EQUAL_AREA)


def zone_income(zones, tracts):
    """Household-weighted mean of tract median incomes over each zone, assuming households are spread
    evenly within a tract (areal interpolation)."""
    t = tracts.dropna(subset=["income", "households"])
    t = t[t["households"] > 0]
    sindex = t.sindex
    result = []
    for z in zones:
        num_ = den = 0.0
        for j in sindex.query(z, predicate="intersects"):
            row = t.iloc[j]
            share = row.geometry.intersection(z).area / row.geometry.area
            w = row["households"] * share
            num_ += w * row["income"]
            den += w
        result.append(round(num_ / den) if den else None)
    return result


def spearman(xs, ys):
    rho, p = stats.spearmanr(xs, ys)
    return {"rho": round(float(rho), 3), "p": float(f"{p:.2g}"), "n": len(xs)}


def main():
    schools, skipped = match_schools()
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
    zones = build_zones(schools, division_territories(schools, tracts_ea))
    for s, inc in zip(schools, zone_income(zones, tracts_ea)):
        s["zone_income"] = inc
    zone_gdf = gpd.GeoDataFrame({"ncessch": [s["ncessch"] for s in schools]}, geometry=zones)
    zone_gdf["geometry"] = zone_gdf.geometry.simplify(120)
    for s in schools:
        del s["_gslo"], s["_gshi"]

    area = tracts_ea.copy()
    area["geometry"] = area.geometry.simplify(80)  # metres (Albers)
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
    summary = {"n_schools": len(schools), "n_vdoe": len(schools) + len(skipped),
               "unmapped": [f"{s['name']} ({s['division']})" for s in skipped],
               "measures": measures, "by_category": {}, "tests": {}, "score_tests": {}}
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
        sp = spearman([s[m] for s in xs], [s["rank"] for s in xs])
        h, kp = stats.kruskal(*[[s[m] for s in xs if s["rank"] == r] for r in RANK.values()])
        summary["tests"][m] = {"spearman_rho": sp["rho"], "spearman_p": sp["p"],
                               "kruskal_H": round(h, 2), "kruskal_p": float(f"{kp:.2g}"), "n": sp["n"]}
        ys = [s for s in schools if s[m] is not None and s["score"] is not None]
        summary["score_tests"][m] = spearman([s[m] for s in ys], [s["score"] for s in ys])

    OUT.mkdir(parents=True, exist_ok=True)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(s["lon"], 6), round(s["lat"], 6)]},
        "properties": {k: v for k, v in s.items() if k not in ("lat", "lon")},
    } for s in schools]
    json.dump({"type": "FeatureCollection", "features": features},
              open(OUT / "schools.geojson", "w"), separators=(",", ":"))
    area[["GEOID", "NAMELSAD", "income", "moe", "geometry"]].to_file(
        OUT / "tracts.geojson", driver="GeoJSON", COORDINATE_PRECISION=4)
    zone_gdf.to_crs("EPSG:4326").to_file(OUT / "zones.geojson", driver="GeoJSON", COORDINATE_PRECISION=4)
    json.dump(summary, open(OUT / "summary.json", "w"), indent=1, default=lambda v: round(v, 3))

    with open(BUILD / "schools_matched.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(schools[0].keys()))
        w.writeheader()
        w.writerows(schools)

    # Console report for the README.
    print(f"{len(schools)} schools mapped ({len(skipped)} unmapped), {len(area)} tracts")
    for m, t in summary["tests"].items():
        print("category rank vs", m, t)
    for m, t in summary["score_tests"].items():
        print("framework score vs", m, t)
    for cat, e in summary["by_category"].items():
        print(f"{cat:26} n={e['n']:4}", *(f"{m}={e[m]['median']}" for m in measures))
    groups = {
        "region": lambda s: f"{s['region']} {s['region_name']}",
        "level": lambda s: s["level"],
        "study area": lambda s: "original study area" if s["study_area"] else "rest of state",
        "division": lambda s: s["division"],
    }
    for label, key in groups.items():
        by = defaultdict(list)
        for s in rated:
            if s["zone_income"]:
                by[key(s)].append(s)
        res = [(g, spearman([s["zone_income"] for s in xs], [-s["rank"] for s in xs]))
               for g, xs in by.items() if len(xs) >= (15 if label == "division" else 10)]
        res.sort(key=lambda r: -r[1]["rho"])
        if label == "division":
            res = res[:8] + [("...", None)] + res[-5:]
        print(f"\nzone income vs category (rho>0: richer = better), by {label}:")
        for g, r in res:
            print(f"  {g:32} {r}" if r else "  ...")
    nis = Counter(s["division"] for s in schools if s["category"] == "Needs Intensive Support")
    print("\nNeeds Intensive Support by division:", nis.most_common(10))

if __name__ == "__main__":
    main()
