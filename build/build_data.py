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
import hashlib
import io
import json
import re
import statistics
import urllib.parse
import urllib.request
import warnings
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import openpyxl
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
SABS_URL = "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/SABS_1516/MapServer/0"
ZONE_SOURCES = BUILD / "zone_sources.json"  # division-published attendance-zone layers
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
GRADE_OVERRIDES = {"510151003155": ("KG", "05"), "510084003146": ("06", "08")}  # new schools NCES lists without grades
# NCES coordinates ~9 km from the school's address; Census geocoder location instead.
LOCATION_FIXES = {"510225001632": (39.036837, -77.478310)}  # Dominion Trail Elementary, Ashburn
# Schools with no NCES 2024-25 record at all: location from the Census geocoder.
MANUAL = {
    ("Prince William County", "Woodbridge Elementary"): {
        "SCH_NAME": "(not in NCES 2024-25)", "LSTREET1": "1550 Prince William Pkwy", "LCITY": "Woodbridge",
        "LZIP": "22191", "GSLO": "KG", "GSHI": "05", "ULOCALE": "21-Suburb: Large", "MEMBER": None,
        "LATCOD": 38.650193, "LONCOD": -77.261943,
    },
}
UNMAPPED = {("Manassas City", "Virginia Virtual Academy at Manassas")}  # no NCES record; rated Too Small

# 2024-25 (year-one) results. VDOE renamed two categories for 2025-26; the score scale is unchanged.
PRIOR_CATEGORY = {"On Track": "Meets Expectations", "Off Track": "Approaching Expectations"}
# 2025-26 schools whose 2024-25 row is under another name. (Mountain View Upper Elementary, Deep Creek
# Middle and Gladys West Elementary are new.)
PRIOR_NAMES = {
    ("Danville City", "Arnett Hills Elementary"): "G.L.H. Johnson Elementary",
    ("Chesapeake City", "B.M. Williams Elementary"): "B.M. Williams Primary",
    ("Chesapeake City", "Crestwood Elementary School"): "Crestwood Intermediate",
}

# Economically disadvantaged students (VDOE's student-level flag) as a share of students taking the
# 2025-26 reading and math SOL tests, from the School Quality Profiles data download.
SQ_URL = "https://schoolquality.virginia.gov/download-data"
ED_FILE = RAW / "sq_econ_disadvantaged_2025_26.csv"
ED_GROUPS = ["All Students", "Economically Disadvantaged", "Not Economically Disadvantaged"]

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
        lat, lon = LOCATION_FIXES.get(rec["NCESSCH"], (rec["LATCOD"], rec["LONCOD"]))
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
            "lat": lat,
            "lon": lon,
        })
    dupes = [k for k, n in Counter(s["ncessch"] for s in out).items() if n > 1]
    if dupes:
        raise SystemExit(f"several VDOE schools matched the same NCES school: {dupes}")
    return out, skipped


def match_by_name(schools, records, override=None):
    """Attach records {division: {name: value}} to schools by exact normalized name, then fuzzy (>= 0.9)
    within the division. Returns the number matched; unmatched schools get None."""
    hits = 0
    for s in schools:
        cands = {norm(n): v for n, v in records.get(s["division"], {}).items()}
        target = norm((override or {}).get((s["division"], s["name"]), s["name"]))
        best = max(cands, key=lambda c: difflib.SequenceMatcher(None, target, c).ratio(), default=None)
        if best is not None and difflib.SequenceMatcher(None, target, best).ratio() >= 0.9:
            s["_rec"] = cands[best]
            hits += 1
        else:
            s["_rec"] = None
    return hits


def load_prior():
    """2024-25 results: {division: {school name: (score, category, federal id)}}."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl styling warnings
        ws = openpyxl.load_workbook(RAW / "spsf_2024_25.xlsx", read_only=True).active
        rows = [[("" if v is None else str(v)).strip() for v in r] for r in ws.iter_rows(values_only=True)]
    start = next(i for i, r in enumerate(rows) if r[0].startswith("Superintendent")) + 1
    out = defaultdict(dict)
    for r in rows[start:]:
        if r[2]:
            cat = r[5] or "Not Rated"
            out[r[1]][r[2]] = (num(r[4]), PRIOR_CATEGORY.get(cat, cat), r[6] or None)
    return out


def fetch_econ_disadvantaged():
    """School-level reading and math test-taker counts by economic status, filtered from the School Quality
    Profiles assessment download (~28 MB) to the rows used here."""
    form = [("level", "schools"), ("divisions[]", "any"), ("schools[]", "any"), ("type", "assessments"),
            ("indicators[]", "ENGR"), ("indicators[]", "MATH"), ("year", "current")]
    req = urllib.request.Request(SQ_URL, data=urllib.parse.urlencode(form).encode(),
                                 headers={"User-Agent": "Mozilla/5.0"})
    print(f"downloading {SQ_URL} (assessments)")
    lines = urllib.request.urlopen(req).read().decode("utf-8-sig").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Year,"))
    rows = list(csv.DictReader(lines[start:]))
    keep = [r for r in rows if not r["Test Level"] and r["Subgroup"] in ED_GROUPS]
    with open(ED_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Year", "Division", "School", "Subject Area", "Subgroup", "Record Count"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(keep)


def load_econ_disadvantaged():
    """{division: {school: (share economically disadvantaged, students tested)}}. Reading and math counts are
    pooled. A suppressed group (count 0 with '<') is inferred as all students minus the other group."""
    if not ED_FILE.exists():
        fetch_econ_disadvantaged()
    counts = defaultdict(Counter)
    for r in csv.DictReader(open(ED_FILE, encoding="utf-8")):
        n = int(r["Record Count"] or 0)
        counts[(r["Division"].removesuffix(" Public Schools"), r["School"])][r["Subgroup"]] += n
    out = defaultdict(dict)
    for (div, school), c in counts.items():
        total, ed, not_ed = (c[g] for g in ED_GROUPS)
        if total < 10:
            continue
        if ed and not_ed:
            share = ed / (ed + not_ed)
        elif ed or not_ed:
            share = ed / total if ed else 1 - not_ed / total
        else:
            continue
        out[div][school] = (round(share, 3), total)
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


def esri_to_geojson(feature):
    """An Esri JSON polygon feature as GeoJSON: clockwise rings are shells, the rest holes."""
    rings = [shapely.LinearRing(r) for r in feature["geometry"]["rings"] if len(r) >= 4]
    shells = [shapely.Polygon(r) for r in rings if not r.is_ccw]
    holes = [shapely.Polygon(r) for r in rings if r.is_ccw]
    polys = []
    for sh in shells:
        inner = [h.exterior for h in holes if sh.contains(h.representative_point())]
        polys.append(shapely.Polygon(sh.exterior, inner))
    geom = shapely.MultiPolygon(polys) if polys else shapely.Polygon()
    return {"type": "Feature", "properties": feature["attributes"], "geometry": shapely.geometry.mapping(geom)}


def fetch_arcgis(url, where, out_fields, path):
    """All features of an ArcGIS REST layer as a GeoJSON file in WGS84 (cached)."""
    if path.exists():
        return path
    print(f"downloading {url} ({where})")
    features, offset = [], 0
    while True:
        params = {"where": where, "outFields": out_fields, "outSR": 4326, "f": "json", "returnGeometry": "true",
                  "resultOffset": offset, "resultRecordCount": 1000}
        req = urllib.request.Request(f"{url}/query?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        page = json.load(urllib.request.urlopen(req, timeout=300))
        if "error" in page:
            raise SystemExit(f"{url}: {page['error']}")
        batch = [esri_to_geojson(f) for f in page["features"] if f.get("geometry")]
        features += batch
        offset += len(page["features"])
        if not page.get("exceededTransferLimit") or not page["features"]:
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"type": "FeatureCollection", "features": features}, open(path, "w"))
    return path


def fetch_table(url, fields, path):
    """Attribute rows of an ArcGIS REST table or layer (cached JSON)."""
    if not path.exists():
        params = {"where": "1=1", "outFields": fields, "returnGeometry": "false", "f": "json"}
        req = urllib.request.Request(f"{url}/query?{urllib.parse.urlencode(params)}", headers={"User-Agent": "Mozilla/5.0"})
        rows = [f["attributes"] for f in json.load(urllib.request.urlopen(req, timeout=300))["features"]]
        path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(path, "w"))
    return json.load(open(path))


def zone_key(name):
    """School name reduced for matching against zone-layer labels ('Oak Hill ES' ~ 'Oak Hill Elementary')."""
    s = norm(re.sub(r"\(.*?\)", " ", str(name)))
    s = re.sub(r"\b(pk|k|\d+) \d+\b", " ", s)  # grade-span suffixes: 'Palmer K-5'
    s = re.sub(r"\b(elementary|elem|es|middle|ms|high|hs|intermediate|upper|primary|lower|center|sch|"
               r"public|county|city|zone|attendance|area|boundary|district)\b", " ", s)
    return " ".join(s.split())


def initials(key):
    """'robert s payne' -> 'rspayne', matching layer labels such as 'RS PAYNE' -> 'rspayne'."""
    w = key.split()
    return "".join(x[0] for x in w[:-1]) + w[-1] if w else ""


def load_official_zones():
    """{division: [layer]} from zone_sources.json, each layer {label, url, polys: {zone_key: geometry}}."""
    layers = defaultdict(list)
    for src in json.load(open(ZONE_SOURCES)):
        tag = hashlib.md5(f"{src['url']} {src['name_field']} {src.get('where')}".encode()).hexdigest()[:6]
        slug = re.sub(r"[^a-z0-9]+", "_", f"{src['division']} {src['level']}".lower()) + "_" + tag
        path = fetch_arcgis(src["url"], src.get("where") or "1=1", src["name_field"], CACHE / "zones" / f"{slug}.geojson")
        gdf = gpd.read_file(path)
        if gdf.empty:
            raise SystemExit(f"no features in {src['url']}")
        gdf = gdf.set_crs("EPSG:4326", allow_override=True).to_crs(EQUAL_AREA)
        gdf["geometry"] = gdf.geometry.make_valid()
        names = gdf[src["name_field"]].astype(str).str.strip()
        if "name_lookup" in src:  # layer holds only school codes (Loudoun)
            lk = src["name_lookup"]
            rows = fetch_table(lk["url"], f"{lk['code_field']},{lk['name_field']}", CACHE / "zones" / f"{slug}_names.json")
            codes = {str(r[lk["code_field"]]).strip(): r[lk["name_field"]] for r in rows}
            names = names.map(lambda c: codes.get(c, c))
        if "split" in src:  # one field naming two levels: 'Liberty Middle - Patrick Henry High'
            sp = src["split"]
            names = names.map(lambda n: n.split(sp["sep"])[sp["part"]] if sp["sep"] in n else n)
        gdf["key"] = names.map(zone_key)
        gdf["full"] = names.map(norm)
        polys = {k: shapely.union_all(g.geometry.values) for k, g in gdf.groupby("key") if k}
        full = {k: shapely.union_all(g.geometry.values) for k, g in gdf.groupby("full") if k}
        layers[src["division"]].append({"label": src["label"], "level": src["level"], "url": src["url"],
                                        "polys": polys, "full": full, "aliases": src.get("aliases", {})})
    return layers


def layer_match(layer, school):
    """(confidence, polygon) for this school in a zone layer, or None. Only layers for one of the school's
    levels are searched (a K-8 'ES-MS' school can match an ES or MS layer). Confidence 4: alias; 3: full
    name; 2: name without level words; 1: fuzzy (>= 0.85), initials ('RS PAYNE') or one name nested in the
    other ('Archer' ~ 'Louise Archer')."""
    if layer["level"] not in school["model"].replace(" ", "").split("-"):
        return None
    alias = layer["aliases"].get(school["name"])
    if alias is not None:
        poly = layer["full"].get(norm(alias))
        poly = poly if poly is not None else layer["polys"].get(zone_key(alias))
        return (4, poly) if poly is not None else None
    if norm(school["name"]) in layer["full"]:
        return 3, layer["full"][norm(school["name"])]
    key = zone_key(school["name"])
    if key in layer["polys"]:
        return 2, layer["polys"][key]
    if not key:
        return None
    best = max(layer["polys"], key=lambda k: difflib.SequenceMatcher(None, key, k).ratio())
    if difflib.SequenceMatcher(None, key, best).ratio() >= 0.85:
        return 1, layer["polys"][best]
    by_initials = [k for k in layer["polys"] if k.replace(" ", "") == initials(key)]
    if len(by_initials) == 1:
        return 1, layer["polys"][by_initials[0]]
    words = set(key.split())
    nested = [k for k in layer["polys"] if words <= set(k.split()) or set(k.split()) <= words]
    return (1, layer["polys"][nested[0]]) if len(nested) == 1 else None


def load_sabs():
    """NCES SABS 2015-16 zones for Virginia: {LEAID: {NCESSCH: (geometry, grades)}}; open-enrollment
    boundaries are skipped."""
    path = fetch_arcgis(SABS_URL, "stAbbrev='VA'", "ncessch,leaid,gslo,gshi,openEnroll", CACHE / "zones" / "sabs_1516_va.geojson")
    gdf = gpd.read_file(path).to_crs(EQUAL_AREA)
    gdf = gdf[gdf["openEnroll"].astype(str) != "1"]
    gdf["geometry"] = gdf.geometry.make_valid()
    out = defaultdict(dict)
    for (lea, sch), g in gdf.groupby(["leaid", "ncessch"]):
        r = g.iloc[0]
        lo, hi = ("KG" if v == "PK" else v for v in (r["gslo"], r["gshi"]))
        grades = set(GRADES[GRADES.index(lo):GRADES.index(hi) + 1]) if lo in GRADES and hi in GRADES else set()
        out[lea][sch] = (shapely.union_all(g.geometry.values), grades)
    return out


def build_zones(schools, territory, official, sabs):
    """Attendance zones, built grade by grade within each division from the best source available:
      1. the division's own published zone layer, when it has polygons for at least half the schools
         serving that grade (schools it lacks, typically magnets/alternatives, get no zone for that grade);
      2. NCES SABS 2015-16, when its schools for that grade are exactly today's schools;
      3. otherwise nearest-school (Voronoi) cells of the division's territory.
    A school's zone is the union of its per-grade cells; a school with none (virtual, or missing from an
    official layer) gets the whole division. Returns (zones, sources) in EQUAL_AREA."""
    pts = gpd.GeoSeries(gpd.points_from_xy([s["lon"] for s in schools], [s["lat"] for s in schools]),
                        crs="EPSG:4269").to_crs(EQUAL_AREA)
    by_div = defaultdict(list)
    for i, s in enumerate(schools):
        by_div[s["division"]].append(i)

    zones, sources = [None] * len(schools), [None] * len(schools)
    usage, missing = Counter(), defaultdict(set)
    for div, idx in by_div.items():
        terr = territory[div]
        cells, src = defaultdict(dict), defaultdict(set)
        leas = {schools[i]["ncessch"][:7] for i in idx}
        sabs_div = {k: v for lea in leas for k, v in sabs.get(lea, {}).items()}
        # Each school's best match across the division's layers: (confidence, polygon, label).
        match = {}
        for i in idx:
            found = [(m[0], m[1], layer["label"]) for layer in official.get(div, [])
                     if (m := layer_match(layer, schools[i])) is not None]
            match[i] = max(found, key=lambda f: f[0]) if found else None
        for g in GRADES:
            serving = [i for i in idx if g in (grades_served(schools[i]) or [])]
            if not serving:
                continue
            hit = [i for i in serving if match[i]]
            if hit and len(hit) >= len(serving) / 2:
                for i in hit:
                    poly = match[i][1]
                    cells[i][id(poly)] = poly
                    src[i].add(("official", match[i][2]))
                missing[div].update(schools[i]["name"] for i in serving if not match[i])
                usage["official"] += 1
                continue
            in_sabs = {n: geom for n, (geom, grades) in sabs_div.items() if g in grades}
            if in_sabs and set(in_sabs) == {schools[i]["ncessch"] for i in serving}:
                for i in serving:
                    geom = in_sabs[schools[i]["ncessch"]]
                    cells[i][id(geom)] = geom
                    src[i].add(("sabs", "NCES School Attendance Boundary Survey 2015-16"))
                usage["sabs"] += 1
                continue
            usage["voronoi"] += 1
            if len(serving) == 1:
                cells[serving[0]][id(terr)] = terr
                src[serving[0]].add(("voronoi", ""))
                continue
            vor = shapely.voronoi_polygons(shapely.MultiPoint([pts[i] for i in serving]), extend_to=terr)
            for i in serving:
                cell = min(vor.geoms, key=lambda c: c.distance(pts[i]))  # duplicate points share a cell
                cells[i][id(cell)] = cell
                src[i].add(("voronoi", ""))
        for i in idx:
            if cells[i]:
                zones[i] = shapely.union_all(list(cells[i].values())).intersection(terr)
                sources[i] = sorted(src[i])
            else:
                zones[i], sources[i] = terr, [("division", "")]
            if zones[i].is_empty:  # official polygon entirely outside the division's territory
                zones[i], sources[i] = terr, [("division", "")]
    print("division-grades by zone source:", dict(usage))
    for div, names in sorted(missing.items()):
        if names:
            print(f"  no zone in {div}'s layers: {'; '.join(sorted(names))}")
    outside = [schools[i]["name"] for i in range(len(schools)) if not zones[i].buffer(1).contains(pts[i])]
    print(f"{len(outside)} schools sit outside their own zone:", "; ".join(outside[:20]))
    return gpd.GeoSeries(zones, crs=EQUAL_AREA), sources


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

    n = match_by_name(schools, load_prior(), PRIOR_NAMES)
    for s in schools:
        score, cat, fed = s.pop("_rec") or (None, None, None)
        s["prior_score"], s["prior_category"], s["prior_federal_id"] = score, cat, fed
        s["score_change"] = round(s["score"] - score, 1) if s["score"] is not None and score is not None else None
    print(f"2024-25 results matched for {n} schools; new or unmatched:",
          "; ".join(f"{s['name']} ({s['division']})" for s in schools if s["prior_category"] is None))

    n = match_by_name(schools, load_econ_disadvantaged())
    for s in schools:
        share, tested = s.pop("_rec") or (None, None)
        s["econ_disadv"], s["econ_tested"] = share, tested
    print(f"economically disadvantaged share for {n} schools")

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
    zones, sources = build_zones(schools, division_territories(schools, tracts_ea), load_official_zones(), load_sabs())
    for s, inc, src in zip(schools, zone_income(zones, tracts_ea), sources):
        s["zone_income"] = inc
        # Weakest source wins the label: a K-8 school with official K-5 and Voronoi 6-8 cells is "voronoi".
        kinds = [k for k, _ in src]
        s["zone_source"] = next(k for k in ("division", "voronoi", "sabs", "official") if k in kinds)
        s["zone_source_name"] = "; ".join(sorted({label for k, label in src if k == "official"})) or None
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
                "zone_income_vs_division": "Zone income relative to the division median",
                "econ_disadv": "Economically disadvantaged share of tested students"}
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
    print("zone sources:", Counter(s["zone_source"] for s in schools))
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

    both = [s for s in rated if s["econ_disadv"] is not None and s["zone_income"]]
    print("\nzone income vs econ. disadvantaged share:", spearman([s["zone_income"] for s in both],
                                                             [s["econ_disadv"] for s in both]))
    by = defaultdict(list)
    for s in both:
        by[f"{s['region']} {s['region_name']}"].append(s)
    print("econ. disadvantaged vs score (rho<0: poorer = lower), by region:")
    for g, xs in sorted(by.items()):
        xs = [s for s in xs if s["score"] is not None]
        print(f"  {g:32} {spearman([s['econ_disadv'] for s in xs], [s['score'] for s in xs])}")

    changed = [s for s in schools if s["score_change"] is not None]
    print(f"\nscore change 2024-25 -> 2025-26: n={len(changed)}, median {statistics.median(s['score_change'] for s in changed)},"
          f" improved {sum(s['score_change'] > 0 for s in changed)}")
    trans = Counter((s["prior_category"], s["category"]) for s in schools if s["prior_category"] and s["rank"])
    for a in CATEGORIES[:4]:
        print(f"  {a:26}", [trans[(a, b)] for b in CATEGORIES[:4]])
    for m in measures:
        xs = [s for s in changed if s[m] is not None]
        print(f"  change vs {m}:", spearman([s[m] for s in xs], [s["score_change"] for s in xs]))

if __name__ == "__main__":
    main()
