// School performance map — Leaflet + hand-built SVG charts. Data built by build/build_data.py.

const CATS = [
  { key: "Distinguished", var: "--cat-1", rank: 1 },
  { key: "Meets Expectations", var: "--cat-2", rank: 2 },
  { key: "Approaching Expectations", var: "--cat-3", rank: 3 },
  { key: "Needs Intensive Support", var: "--cat-4", rank: 4 },
  { key: "Too Small", var: "--cat-0" },
  { key: "Not Rated", var: "--cat-0" },
];
const LEVELS = ["Elementary", "Middle", "High", "Combined", "Virtual"];
const MEASURES = {
  zone_income: { label: "Zone income", long: "household-weighted median income of the approximate attendance zone", fmt: money },
  tract_income: { label: "School's tract", long: "median household income of the census tract the school sits in", fmt: money },
  zone_income_vs_division: { label: "Relative to division", long: "zone income ÷ the division's median zone income (1.0 = typical for that division)", fmt: v => v.toFixed(2) + "×" },
};
const VIEWS = { strip: "By category", scatter: "Score vs. income" };
const FEDERAL = { CSI: "Comprehensive Support & Improvement", TSI: "Targeted Support & Improvement", ATSI: "Additional Targeted Support & Improvement" };
const SCORE_CUTS = [65, 80, 90]; // approximate framework-score thresholds between categories
const INCOME_BREAKS = [50000, 65000, 80000, 100000, 130000];
const INCOME_LABELS = ["&lt;$50k", "50–65", "65–80", "80–100", "100–130", "$130k+"];
const RAMP_LIGHT = ["#f3f2ee", "#dcdad3", "#c3c2b7", "#a6a49b", "#807e78", "#5c5b57"];
const RAMP_DARK = ["#232322", "#30302e", "#43423f", "#5c5b57", "#7d7b75", "#a3a199"];

const state = {
  cats: new Set(CATS.map(c => c.key)),
  levels: new Set(LEVELS),
  region: "",
  division: "",
  measure: "zone_income",
  view: "strip",
  selected: null,
};

const $ = id => document.getElementById(id);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isDark = () => matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light"
  || document.documentElement.dataset.theme === "dark";
const catColor = cat => css(CATS.find(c => c.key === cat).var);
const levelOf = p => (LEVELS.includes(p.level) ? p.level : "Elementary");
function money(v) { return v == null ? "—" : v > 250000 ? "$250k+" : "$" + Math.round(v / 1000) + "k"; }
function moneyFull(v) { return v == null ? "—" : v > 250000 ? "$250,000+" : "$" + v.toLocaleString(); }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------- Map ----------
const map = L.map("map", { zoomControl: true }).setView([37.8, -78.8], 7);
map.createPane("tracts").style.zIndex = 350;
map.createPane("zones").style.zIndex = 380;
map.createPane("labels").style.zIndex = 420;
map.getPane("labels").style.pointerEvents = "none";
const markerRenderer = L.canvas({ padding: 0.5, tolerance: 3 });
const attribution = 'Basemap &copy; <a href="https://www.esri.com">Esri</a>, HERE, Garmin, &copy; OpenStreetMap contributors · Census ACS · NCES · VDOE';
let baseLayer, labelLayer;
function setBasemap() {
  const flavor = isDark() ? "Dark" : "Light";
  const esri = layer => `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${flavor}_Gray_${layer}/MapServer/tile/{z}/{y}/{x}`;
  if (baseLayer) map.removeLayer(baseLayer);
  if (labelLayer) map.removeLayer(labelLayer);
  baseLayer = L.tileLayer(esri("Base"), { attribution, maxZoom: 16 }).addTo(map);
  labelLayer = L.tileLayer(esri("Reference"), { pane: "labels", maxZoom: 16 }).addTo(map);
}

let schools = [], tractLayer, zoneData = new Map(), zoneLayer = L.layerGroup(), selectedZone, summary;
const markers = new Map();

function incomeColor(v) {
  const ramp = isDark() ? RAMP_DARK : RAMP_LIGHT;
  if (v == null) return "transparent";
  let i = INCOME_BREAKS.findIndex(b => v < b);
  return ramp[i === -1 ? ramp.length - 1 : i];
}

function tractStyle(f) {
  return { pane: "tracts", fillColor: incomeColor(f.properties.income), fillOpacity: 0.6, color: css("--surface"), weight: 0.5, opacity: 0.6 };
}

function drawRampLegend() {
  const ramp = isDark() ? RAMP_DARK : RAMP_LIGHT;
  $("ramp").innerHTML = ramp.map(c => `<span style="background:${c}"></span>`).join("");
  $("ramp-ticks").innerHTML = INCOME_LABELS.map(t => `<span style="flex:1;text-align:center">${t}</span>`).join("");
}

function markerStyle(p, highlighted) {
  const z = map.getZoom();
  return {
    renderer: markerRenderer,
    radius: (highlighted ? 3 : 0) + (z <= 7 ? 3 : z <= 8 ? 4 : z <= 10 ? 5 : 7) + (p.level === "High" ? 1 : 0),
    fillColor: catColor(p.category), fillOpacity: 1,
    color: highlighted ? css("--ink") : css("--ring"), weight: highlighted ? 3 : z <= 7 ? 1 : 1.5,
  };
}

function componentRows(p) {
  return Object.entries(p.components || {}).map(([k, [earned, possible]]) =>
    `<dt>${k[0].toUpperCase() + k.slice(1)}</dt><dd>${earned == null ? "—" : earned.toFixed(1)} <span class="note">/ ${possible}</span></dd>`).join("");
}

function popupHtml(p) {
  const rel = p.zone_income_vs_division;
  return `<div class="pop">
    <h3>${esc(p.name)}</h3>
    <div class="note">${esc(p.division)} · Region ${p.region} (${esc(p.region_name)}) · ${esc(p.level)} · grades ${esc(p.grades)}</div>
    <div class="badge"><span class="swatch" style="background:${catColor(p.category)}"></span>${esc(p.category)}${p.score != null ? ` <span class="note">· score ${p.score.toFixed(1)}</span>` : ""}</div>
    <dl>
      ${componentRows(p)}
      ${p.federal_id ? `<dt>Federal ID</dt><dd><abbr title="${esc(FEDERAL[p.federal_id] || p.federal_id)}">${esc(p.federal_id)}</abbr></dd>` : ""}
      <dt class="sep">Enrollment</dt><dd class="sep">${p.enrollment ?? "—"}</dd>
      <dt>Locale</dt><dd>${esc(p.locale || "—")}</dd>
      <dt>Zone income</dt><dd>${moneyFull(p.zone_income)}</dd>
      <dt>vs. division</dt><dd>${rel == null ? "—" : rel.toFixed(2) + "×"}</dd>
      <dt>Tract income</dt><dd>${moneyFull(p.tract_income)}${p.tract_income_moe ? ` <span class="note">±${p.tract_income_moe.toLocaleString()}</span>` : ""}</dd>
      <dt>Address</dt><dd style="text-align:left">${esc(p.address)}</dd>
    </dl>
  </div>`;
}

function selectSchool(id, { fly = false } = {}) {
  const prev = state.selected;
  state.selected = id;
  if (prev && markers.has(prev)) markers.get(prev).setStyle(markerStyle(markers.get(prev).feature.properties, false));
  if (selectedZone) { map.removeLayer(selectedZone); selectedZone = null; }
  if (!id) { renderChart(); return; }
  const m = markers.get(id);
  m.setStyle(markerStyle(m.feature.properties, true)).bringToFront();
  const z = zoneData.get(id);
  if (z) {
    selectedZone = L.geoJSON(z, { pane: "zones", interactive: false, style: { color: css("--ink"), weight: 2.5, dashArray: "6 4", fillColor: catColor(m.feature.properties.category), fillOpacity: 0.12 } }).addTo(map);
    if (fly) map.flyToBounds(selectedZone.getBounds().pad(0.15), { maxZoom: 13, duration: 0.6 });
  } else if (fly) map.flyTo(m.getLatLng(), 13);
  m.openPopup();
  renderChart();
}

// ---------- Filters ----------
function inRegion(p) {
  return !state.region || (state.region === "study" ? p.study_area : String(p.region) === state.region);
}
function inScope(p) { return inRegion(p) && (!state.division || p.division === state.division); }
function visible(p) { return state.cats.has(p.category) && state.levels.has(levelOf(p)) && inScope(p); }
function filtered() { return schools.filter(f => visible(f.properties)); }

function flyToFiltered() {
  const fs = filtered();
  if (fs.length) map.flyToBounds(L.latLngBounds(fs.map(f => markers.get(f.properties.ncessch).getLatLng())).pad(0.1), { maxZoom: 12, duration: 0.6 });
}

function fillDivisions() {
  const divs = [...new Set(schools.filter(f => inRegion(f.properties)).map(f => f.properties.division))].sort();
  if (!divs.includes(state.division)) state.division = "";
  $("division").innerHTML = `<option value="">All divisions${state.region ? " in view" : ""}</option>` +
    divs.map(d => `<option${d === state.division ? " selected" : ""}>${esc(d)}</option>`).join("");
}

function buildControls() {
  // Category rows with counts
  $("cats").innerHTML = CATS.map(c => `
    <label class="row"><input type="checkbox" data-cat="${esc(c.key)}" checked>
      <span class="swatch" style="background:var(${c.var})"></span>
      <span>${c.key}</span><span class="count" data-count="${esc(c.key)}"></span></label>`).join("");
  $("cats").addEventListener("change", e => {
    const k = e.target.dataset.cat; if (!k) return;
    e.target.checked ? state.cats.add(k) : state.cats.delete(k); update();
  });
  $("cats-all").onclick = () => { CATS.forEach(c => state.cats.add(c.key)); syncCats(); update(); };
  $("cats-none").onclick = () => { state.cats.clear(); syncCats(); update(); };

  $("levels").innerHTML = LEVELS.map(l => `<label class="chip"><input type="checkbox" data-level="${l}" checked>${l}</label>`).join("");
  $("levels").addEventListener("change", e => {
    const k = e.target.dataset.level; e.target.checked ? state.levels.add(k) : state.levels.delete(k); update();
  });

  const regions = [...new Map(schools.map(f => [f.properties.region, f.properties.region_name]))].sort((a, b) => a[0] - b[0]);
  $("region").innerHTML = `<option value="">All of Virginia</option>` +
    regions.map(([n, name]) => `<option value="${n}">Region ${n} · ${esc(name)}</option>`).join("") +
    `<option value="study">Original study area (SW &amp; Southside, 47 divisions)</option>`;
  $("region").onchange = e => { state.region = e.target.value; fillDivisions(); update(); flyToFiltered(); };

  fillDivisions();
  $("division").onchange = e => { state.division = e.target.value; update(); flyToFiltered(); };

  $("school-names").innerHTML = schools.map(f => `<option value="${esc(f.properties.name)} — ${esc(f.properties.division)}">`).join("");
  $("search").addEventListener("change", e => {
    const hit = schools.find(f => `${f.properties.name} — ${f.properties.division}` === e.target.value)
      || schools.find(f => f.properties.name.toLowerCase().includes(e.target.value.toLowerCase()));
    if (!hit) return;
    const p = hit.properties;
    // Make sure the school is visible before selecting it.
    state.cats.add(p.category); state.levels.add(levelOf(p));
    if (!inScope(p)) { state.region = ""; state.division = ""; $("region").value = ""; fillDivisions(); }
    syncCats(); update();
    selectSchool(p.ncessch, { fly: true });
  });

  $("show-tracts").onchange = e => { e.target.checked ? tractLayer.addTo(map) : map.removeLayer(tractLayer); $("income-legend").style.opacity = e.target.checked ? 1 : 0.4; };
  $("show-zones").onchange = () => drawZones();

  const seg = (el, opts, key) => {
    el.innerHTML = Object.entries(opts).map(([k, v]) => `<button type="button" data-k="${k}" aria-pressed="${k === state[key]}">${typeof v === "string" ? v : v.label}</button>`).join("");
    el.onclick = e => {
      const k = e.target.dataset.k; if (!k) return;
      state[key] = k;
      [...el.children].forEach(b => b.setAttribute("aria-pressed", b.dataset.k === k));
      renderChart();
    };
  };
  seg($("view"), VIEWS, "view");
  seg($("measure"), MEASURES, "measure");
}

function syncCats() {
  $("cats").querySelectorAll("input").forEach(i => (i.checked = state.cats.has(i.dataset.cat)));
  $("levels").querySelectorAll("input").forEach(i => (i.checked = state.levels.has(i.dataset.level)));
}

function drawZones() {
  zoneLayer.clearLayers();
  if (!$("show-zones").checked) { map.removeLayer(zoneLayer); return; }
  for (const f of filtered()) {
    const z = zoneData.get(f.properties.ncessch);
    if (z) zoneLayer.addLayer(L.geoJSON(z, { pane: "zones", interactive: false, style: { color: catColor(f.properties.category), weight: 1.5, opacity: 0.9, fillColor: catColor(f.properties.category), fillOpacity: 0.18 } }));
  }
  zoneLayer.addTo(map);
}

function update() {
  const scope = schools.filter(f => state.levels.has(levelOf(f.properties)) && inScope(f.properties));
  for (const c of CATS) {
    const n = scope.filter(f => f.properties.category === c.key).length;
    const el = $("cats").querySelector(`[data-count="${CSS.escape(c.key)}"]`);
    el.textContent = n;
    el.closest("label").style.display = n || c.rank ? "" : "none";
  }
  for (const f of schools) {
    const m = markers.get(f.properties.ncessch);
    if (visible(f.properties)) { if (!map.hasLayer(m)) m.addTo(map); }
    else if (map.hasLayer(m)) { map.removeLayer(m); if (state.selected === f.properties.ncessch) selectSchool(null); }
  }
  drawZones();
  renderChart();
}

// ---------- Charts ----------
const tip = $("tip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.style.display = "block";
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(x + 14, innerWidth - r.width - 8) + "px";
  tip.style.top = Math.max(8, y - r.height - 10) + "px";
}
const hideTip = () => (tip.style.display = "none");

function quantile(sorted, q) {
  const i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}
function ranks(xs) {
  const idx = xs.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]), r = new Array(xs.length);
  for (let i = 0; i < idx.length;) {
    let j = i; while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++;
    for (let k = i; k <= j; k++) r[idx[k][1]] = (i + j) / 2 + 1;
    i = j + 1;
  }
  return r;
}
function spearman(a, b) {
  const ra = ranks(a), rb = ranks(b), n = a.length, ma = (n + 1) / 2;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) { num += (ra[i] - ma) * (rb[i] - ma); da += (ra[i] - ma) ** 2; db += (rb[i] - ma) ** 2; }
  return num / Math.sqrt(da * db);
}
const strength = r => (Math.abs(r) < 0.1 ? "negligible" : Math.abs(r) < 0.3 ? "weak" : Math.abs(r) < 0.5 ? "moderate" : "strong");
const hash = s => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return ((h >>> 0) % 1000) / 1000; };

// Income measures span $20k–$250k+, so the x axis is logarithmic.
function xAxis(m, left, right, W, narrow) {
  const rel = m === "zone_income_vs_division";
  const lo = rel ? 0.4 : 20000, hi = rel ? 2.5 : 250000;
  const ticks = rel ? (narrow ? [0.5, 1, 2] : [0.5, 0.75, 1, 1.5, 2])
    : (narrow ? [25000, 50000, 100000, 200000] : [25000, 35000, 50000, 70000, 100000, 150000, 200000]);
  const x = v => left + (Math.log(Math.min(Math.max(v, lo), hi)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * (W - left - right);
  return { x, ticks };
}

function renderChart() {
  const m = state.measure, meta = MEASURES[m];
  const data = filtered().map(f => f.properties).filter(p => p.rank && p[m] != null);
  $("panel-count").textContent = `${data.length} rated schools in view`;
  $("chart-title").textContent = state.view === "strip" ? "Income by performance category" : "Framework score vs. income";
  if (state.view === "strip") renderStrip(data); else renderScatter(data);

  let text = `Showing the <b>${meta.long}</b>. `;
  if (state.view === "strip") {
    const med = cat => { const v = data.filter(p => p.category === cat).map(p => p[m]).sort((a, b) => a - b); return v.length >= 2 ? quantile(v, 0.5) : null; };
    const best = state.cats.has("Distinguished") ? med("Distinguished") : null;
    const worst = state.cats.has("Needs Intensive Support") ? med("Needs Intensive Support") : null;
    if (best != null && worst != null) text += `Median for <b>Distinguished</b> schools: <b>${meta.fmt(best)}</b>; for <b>Needs Intensive Support</b>: <b>${meta.fmt(worst)}</b>. `;
    if (data.length >= 10 && new Set(data.map(p => p.rank)).size >= 2) {
      const rho = -spearman(data.map(p => p[m]), data.map(p => p.rank));
      text += `Rank correlation between income and category in view: <b>ρ = ${rho.toFixed(2)}</b> (${strength(rho)}${rho > 0 ? ", higher income ↔ better category" : rho < 0 ? ", higher income ↔ worse category" : ""}).`;
    }
  } else {
    const xs = data.filter(p => p.score != null);
    if (xs.length >= 10) {
      const rho = spearman(xs.map(p => p[m]), xs.map(p => p.score));
      text += `Rank correlation between income and framework score in view: <b>ρ = ${rho.toFixed(2)}</b> (${strength(rho)}${rho > 0 ? ", higher income ↔ higher score" : rho < 0 ? ", higher income ↔ lower score" : ""}). Dashed lines mark the approximate category cut-offs (65 / 80 / 90).`;
    }
  }
  $("finding").innerHTML = text;
  renderTable(CATS.filter(c => c.rank && state.cats.has(c.key)), data);
}

function renderStrip(data) {
  const svg = $("chart"), m = state.measure, meta = MEASURES[m];
  const rows = CATS.filter(c => c.rank && state.cats.has(c.key));
  const W = svg.clientWidth || 800, narrow = W < 560, dense = data.length > 500;
  const rowH = dense ? 50 : 34, left = narrow ? 92 : 190, right = narrow ? 24 : 20, top = 8, bottom = 24, r = dense ? 3 : 4;
  const SHORT = { "Distinguished": "Distinguished", "Meets Expectations": "Meets", "Approaching Expectations": "Approaching", "Needs Intensive Support": "Intensive" };
  const H = top + bottom + rowH * Math.max(rows.length, 1);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
  const { x, ticks } = xAxis(m, left, right, W, narrow);

  let out = "";
  for (const t of ticks) out += `<line class="gridline" x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - bottom}"/><text x="${x(t)}" y="${H - 8}" text-anchor="middle">${meta.fmt(t)}</text>`;
  rows.forEach((c, i) => {
    const y0 = top + i * rowH, cy = y0 + rowH / 2;
    const vals = data.filter(p => p.category === c.key).map(p => p[m]).sort((a, b) => a - b);
    out += `<text class="cat-label" x="${left - 12}" y="${cy + 4}" text-anchor="end">${narrow ? SHORT[c.key] : `${c.key} <tspan fill="${css("--muted")}">(${vals.length})</tspan>`}</text>`;
    if (vals.length >= 2) {
      const q1 = quantile(vals, 0.25), q3 = quantile(vals, 0.75), med = quantile(vals, 0.5);
      out += `<rect class="iqr" x="${x(q1)}" y="${y0 + 4}" width="${Math.max(2, x(q3) - x(q1))}" height="${rowH - 8}" rx="4"/>`;
      out += `<line class="median" x1="${x(med)}" x2="${x(med)}" y1="${y0 + 2}" y2="${y0 + rowH - 2}"/>`;
    }
    for (const p of data.filter(p => p.category === c.key)) {
      const jy = cy + (hash(p.ncessch) - 0.5) * (rowH - 14);
      const hl = p.ncessch === state.selected;
      out += `<circle data-id="${p.ncessch}" cx="${x(p[m]).toFixed(1)}" cy="${jy.toFixed(1)}" r="${hl ? 6 : r}" fill="${catColor(c.key)}" class="${hl ? "hl" : ""}"/>`;
    }
  });
  if (!rows.length) out += `<text x="${W / 2}" y="${top + 20}" text-anchor="middle">Select a performance category to compare.</text>`;
  svg.innerHTML = out;
  svg.querySelector("circle.hl")?.parentNode.appendChild(svg.querySelector("circle.hl"));
}

function renderScatter(data) {
  const svg = $("chart"), m = state.measure, meta = MEASURES[m];
  const pts = data.filter(p => p.score != null);
  const W = svg.clientWidth || 800, narrow = W < 560;
  const left = 44, right = narrow ? 24 : 20, top = 10, bottom = 34, H = narrow ? 260 : 300;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
  const { x, ticks } = xAxis(m, left, right, W, narrow);
  const ylo = 40, yhi = 120, y = v => top + (yhi - Math.min(Math.max(v, ylo), yhi)) / (yhi - ylo) * (H - top - bottom);

  let out = "";
  for (const t of ticks) out += `<line class="gridline" x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H - bottom}"/><text x="${x(t)}" y="${H - bottom + 14}" text-anchor="middle">${meta.fmt(t)}</text>`;
  for (let t = ylo; t <= yhi; t += 20) out += `<line class="gridline" x1="${left}" x2="${W - right}" y1="${y(t)}" y2="${y(t)}"/><text x="${left - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`;
  for (const c of SCORE_CUTS) out += `<line class="cut" x1="${left}" x2="${W - right}" y1="${y(c)}" y2="${y(c)}"/>`;
  out += `<text x="${left}" y="${H - 4}">Framework score (y) · ${esc(meta.label.toLowerCase())}, log scale (x)</text>`;
  const sorted = [...pts].sort((a, b) => b.rank - a.rank); // draw best categories last
  for (const p of sorted) {
    const hl = p.ncessch === state.selected;
    out += `<circle data-id="${p.ncessch}" cx="${x(p[m]).toFixed(1)}" cy="${y(p.score).toFixed(1)}" r="${hl ? 6 : 3}" fill="${catColor(p.category)}" class="${hl ? "hl" : ""}"/>`;
  }
  if (!pts.length) out += `<text x="${W / 2}" y="${H / 2}" text-anchor="middle">No rated schools in view.</text>`;
  svg.innerHTML = out;
  svg.querySelector("circle.hl")?.parentNode.appendChild(svg.querySelector("circle.hl"));
}

function renderTable(rows, data) {
  const m = state.measure, fmt = MEASURES[m].fmt;
  let html = `<table><thead><tr><th>Category</th><th>Schools</th><th>25th pct</th><th>Median</th><th>75th pct</th><th>Min</th><th>Max</th><th>Median score</th></tr></thead><tbody>`;
  for (const c of rows) {
    const grp = data.filter(p => p.category === c.key);
    const v = grp.map(p => p[m]).sort((a, b) => a - b);
    const sc = grp.map(p => p.score).filter(s => s != null).sort((a, b) => a - b);
    const cell = q => (v.length ? fmt(quantile(v, q)) : "—");
    html += `<tr><td><span class="swatch" style="display:inline-block;vertical-align:-1px;margin-right:6px;background:${catColor(c.key)}"></span>${c.key}</td><td>${v.length}</td><td>${cell(0.25)}</td><td>${cell(0.5)}</td><td>${cell(0.75)}</td><td>${cell(0)}</td><td>${cell(1)}</td><td>${sc.length ? quantile(sc, 0.5).toFixed(1) : "—"}</td></tr>`;
  }
  html += `</tbody></table>`;
  if (summary) {
    const t = summary.tests[m], s = summary.score_tests[m];
    html += `<p class="note">All ${t.n} rated schools statewide (ignoring filters): Spearman ρ between income and category = ${(-t.spearman_rho).toFixed(2)} (p = ${t.spearman_p}); Kruskal–Wallis H = ${t.kruskal_H} (p = ${t.kruskal_p}); ρ between income and framework score = ${s.rho.toFixed(2)} (p = ${s.p}). Positive ρ means higher-income areas tend to do better. Correlation is not causation — school poverty, urbanicity and division resources all overlap with income.</p>`;
  }
  $("table").innerHTML = html;
}

$("chart").addEventListener("mousemove", e => {
  const id = e.target.dataset?.id; if (!id) return hideTip();
  const p = markers.get(id).feature.properties;
  showTip(`<b>${esc(p.name)}</b><br>${esc(p.division)}<br>${esc(p.category)}${p.score != null ? ` · score ${p.score.toFixed(1)}` : ""}<br>${MEASURES[state.measure].label}: ${MEASURES[state.measure].fmt(p[state.measure])}`, e.clientX, e.clientY);
});
$("chart").addEventListener("mouseleave", hideTip);
$("chart").addEventListener("click", e => { const id = e.target.dataset?.id; if (id) selectSchool(id, { fly: true }); });
addEventListener("resize", () => renderChart());

// ---------- Load ----------
Promise.all(["data/schools.geojson", "data/tracts.geojson", "data/zones.geojson", "data/summary.json"].map(u => fetch(u).then(r => r.json())))
  .then(([s, tracts, zones, sum]) => {
    summary = sum;
    schools = s.features;
    $("n-total").textContent = schools.length.toLocaleString();
    for (const z of zones.features) zoneData.set(z.properties.ncessch, z);
    setBasemap();
    drawRampLegend();
    map.on("zoomend", () => markers.forEach(m => m.setStyle(markerStyle(m.feature.properties, m.feature.properties.ncessch === state.selected))));
    tractLayer = L.geoJSON(tracts, {
      style: tractStyle,
      onEachFeature: (f, l) => l.bindTooltip(`${f.properties.NAMELSAD}<br>Median household income: <b>${moneyFull(f.properties.income)}</b>${f.properties.moe ? ` ±${f.properties.moe.toLocaleString()}` : ""}`, { sticky: true }),
    }).addTo(map);
    for (const f of schools) {
      const p = f.properties;
      const m = L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], markerStyle(p, false));
      m.feature = f;
      m.bindPopup(() => popupHtml(p), { maxWidth: 320 });
      m.bindTooltip(`${esc(p.name)} · ${esc(p.category)}`, { direction: "top", offset: [0, -6] });
      m.on("click", () => selectSchool(p.ncessch));
      m.on("popupclose", () => { if (state.selected === p.ncessch) selectSchool(null); });
      markers.set(p.ncessch, m);
    }
    buildControls();
    update();
    map.fitBounds(L.latLngBounds(schools.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]])).pad(0.02));
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      setBasemap(); drawRampLegend(); tractLayer.setStyle(tractStyle);
      markers.forEach(m => m.setStyle(markerStyle(m.feature.properties, m.feature.properties.ncessch === state.selected)));
      drawZones(); renderChart();
    });
  })
  .catch(err => { $("map").innerHTML = `<p style="padding:16px">Could not load data (${esc(err.message)}). If opening the file directly, serve the folder instead: <code>python -m http.server</code> from the repo root.</p>`; });
