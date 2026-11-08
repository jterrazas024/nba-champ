// Redesigned odds page: reads data/odds.json and shows it as five swipeable screens.
// Plain script, no dependencies. Every piece of data goes in with textContent or
// setAttribute, never innerHTML, so nothing in the JSON can be read as HTML.
// Team marks are original monograms in the team colors, not official logos.

"use strict";

const STALE_AFTER_HOURS = 48;
const SVG_NS = "http://www.w3.org/2000/svg";

// Primary and secondary color per team (used for crests, seams and dots only).
const TEAM_COLORS = {
  ATL: ["#E03A3E", "#C1D32F"], BOS: ["#007A33", "#BA9653"], BKN: ["#111111", "#FFFFFF"],
  CHA: ["#1D1160", "#00A3B4"], CHI: ["#CE1141", "#111111"], CLE: ["#860038", "#FDBB30"],
  DAL: ["#00538C", "#B8C4CA"], DEN: ["#0E2240", "#FEC524"], DET: ["#C8102E", "#1D42BA"],
  GSW: ["#1D428A", "#FFC72C"], HOU: ["#CE1141", "#C4CED4"], IND: ["#002D62", "#FDBB30"],
  LAC: ["#C8102E", "#1D428A"], LAL: ["#552583", "#FDB927"], MEM: ["#5D76A9", "#12173F"],
  MIA: ["#98002E", "#F9A01B"], MIL: ["#00471B", "#EEE1C6"], MIN: ["#236192", "#78BE20"],
  NOP: ["#0C2340", "#C8102E"], NYK: ["#006BB6", "#F58426"], OKC: ["#007AC1", "#EF3B24"],
  ORL: ["#0077C0", "#C4CED4"], PHI: ["#006BB6", "#ED174C"], PHX: ["#1D1160", "#E56020"],
  POR: ["#E03A3E", "#111111"], SAC: ["#5A2D81", "#63727A"], SAS: ["#C4CED4", "#111111"],
  TOR: ["#CE1141", "#111111"], UTA: ["#002B5C", "#F9A01B"], WAS: ["#002B5C", "#E31837"],
};
const colorsOf = (team) => TEAM_COLORS[team.id] || ["#777777", "#BBBBBB"];

const PAGES = ["home", "rankings", "team", "map", "model"];
const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- Number formatting ----------

const fmtPct1 = (v) => (v * 100).toFixed(1) + "%";
const fmtPct0 = (v) => Math.round(v * 100) + "%";

function fmtSigned1(v) {
  const rounded = Number(v.toFixed(1));
  const text = Math.abs(rounded).toFixed(1);
  return rounded > 0 ? "+" + text : rounded < 0 ? "−" + text : text;
}

// Change since the last update, in percentage points, e.g. "▲0.4".
function formatChange(delta) {
  if (typeof delta !== "number") return { text: "–", cls: "" };
  const points = Math.abs(delta * 100).toFixed(1);
  if (Number(points) === 0) return { text: "–", cls: "" };
  return delta > 0 ? { text: "▲" + points, cls: "up" } : { text: "▼" + points, cls: "down" };
}

// The giant number: whole percent from 10% up, one decimal below that.
const bigText = (p) => (p >= 0.095 ? String(Math.round(p * 100)) : (p * 100).toFixed(1));

// ---------- State and small helpers ----------

const state = { data: null, teams: [], selectedId: null, page: 0, metric: "title_prob", homeView: "table" };

const $ = (id) => document.getElementById(id);
const teamById = (id) => state.teams.find((t) => t.id === id) || null;
const selected = () => teamById(state.selectedId);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs || {}).forEach((k) => node.setAttribute(k, attrs[k]));
  if (parent) parent.appendChild(node);
  return node;
}

function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// A monogram crest: disk in the team color, ink ring, secondary arc, abbreviation.
function crest(team, size) {
  const [c1, c2] = colorsOf(team);
  const svg = svgEl("svg", { viewBox: "0 0 48 48", width: size, height: size, class: "crest", "aria-hidden": "true", focusable: "false" });
  svgEl("circle", { cx: 24, cy: 24, r: 22.2, fill: c1, stroke: "currentColor", "stroke-width": 3 }, svg);
  svgEl("circle", { cx: 24, cy: 24, r: 18, fill: "none", stroke: c2, "stroke-width": 1.8 }, svg);
  const text = svgEl("text", {
    x: 24, y: 29, "text-anchor": "middle", fill: luminance(c1) > 0.45 ? "#111" : "#fff",
    style: "font-family:var(--display);font-size:15px;letter-spacing:0.02em",
  }, svg);
  text.textContent = team.id;
  if (size >= 60) {
    // A big crest reads as a domed button: dark side and a glint on top of the flat badge.
    svgEl("circle", { cx: 24, cy: 24, r: 22.2, fill: "url(#sph-shade)", "pointer-events": "none" }, svg);
    svgEl("circle", { cx: 17, cy: 14.5, r: 8, fill: "url(#sph-spec)", opacity: 0.7, "pointer-events": "none" }, svg);
  }
  return svg;
}

// A shaded sphere (SVG group): speckled lit body, optional detail drawn on its surface (seams, rings),
// then the dark side, bounced light and a glint on top. Gradients live in v2.html and follow the theme.
function sphere(parent, cx, cy, r, drawSurface) {
  const g = svgEl("g", { class: "sphere" }, parent);
  svgEl("circle", { cx: cx, cy: cy, r: r, fill: "url(#sph-base)", filter: "url(#speck-soft)" }, g);
  if (drawSurface) drawSurface(g);
  svgEl("circle", { cx: cx, cy: cy, r: r, fill: "url(#sph-shade)", "pointer-events": "none" }, g);
  svgEl("circle", { cx: cx, cy: cy, r: r, fill: "url(#sph-rim)", "pointer-events": "none" }, g);
  svgEl("circle", { cx: cx - r * 0.36, cy: cy - r * 0.42, r: r * 0.34, fill: "url(#sph-spec)", opacity: 0.8, "pointer-events": "none" }, g);
  svgEl("circle", { cx: cx - r * 0.4, cy: cy - r * 0.47, r: r * 0.13, fill: "url(#sph-spec)", "pointer-events": "none" }, g);
  return g;
}

function row(label, value, opts) {
  const node = el(opts && opts.tag ? opts.tag : "div", "row" + (opts && opts.cls ? " " + opts.cls : ""));
  node.appendChild(el("span", "", label));
  node.appendChild(el("span", "lead"));
  node.appendChild(el("span", "val", value));
  return node;
}

// ---------- The giant number ----------

function setupBig(container) {
  container.textContent = "";
  container.setAttribute("role", "img");
  const num = el("span", "big-num");
  num.appendChild(el("span", "face"));
  const ext = el("span", "ext");
  ext.setAttribute("aria-hidden", "true");
  num.appendChild(ext);
  container.appendChild(num);
  container.appendChild(el("span", "big-pct", "%"));
  container.querySelector(".big-pct").setAttribute("aria-hidden", "true");
  container._value = 0;
}

function paintBig(container, p, finalValue) {
  const text = bigText(p);
  container.querySelector(".face").textContent = text;
  container.querySelector(".ext").textContent = text;
  container.classList.toggle("n3", bigText(finalValue).length >= 3);
}

// Counts the number up (or from its current value) to p.
function updateBig(container, p, fromZero) {
  container.setAttribute("aria-label", (p * 100).toFixed(1) + " percent");
  const token = (container._token = (container._token || 0) + 1);
  const start = fromZero ? 0 : container._value;
  if (REDUCED || Math.abs(p - start) < 0.0005) {
    container._value = p;
    paintBig(container, p, p);
    return;
  }
  const t0 = performance.now();
  const duration = fromZero ? 1000 : 650;
  const tick = (now) => {
    if (token !== container._token) return;
    const k = Math.min(1, (now - t0) / duration);
    const eased = 1 - Math.pow(1 - k, 3);
    container._value = start + (p - start) * eased;
    paintBig(container, container._value, p);
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// ---------- Selection ----------

function select(id) {
  if (!teamById(id)) return;
  state.selectedId = id;
  const team = selected();
  document.documentElement.style.setProperty("--team", colorsOf(team)[0]);
  syncSelectionClasses();
  renderHomeText();
  updateBig($("home-big"), team.title_prob, false);
  renderTeam();
  renderMapSelection();
  syncGraphSelection();
  updateGraphReadout();
}

function syncSelectionClasses() {
  document.querySelectorAll("[data-team]").forEach((node) => {
    const on = node.dataset.team === state.selectedId;
    node.classList.toggle("sel", on);
    if (node.tagName === "BUTTON") node.setAttribute("aria-pressed", String(on));
  });
}

// Tapping the team that is already selected opens its page.
function pick(id) {
  if (id === state.selectedId) go(PAGES.indexOf("team"));
  else select(id);
}

// ---------- Page 1: Home ----------

function buildBall() {
  const art = $("home-art");
  art.textContent = "";
  const svg = svgEl("svg", { viewBox: "0 0 300 300", class: "ball-svg", "aria-hidden": "true", focusable: "false" }, art);
  const defs = svgEl("defs", {}, svg);
  const clip = svgEl("clipPath", { id: "ballclip" }, defs);
  svgEl("circle", { cx: 150, cy: 150, r: 100 }, clip);

  svgEl("ellipse", { class: "shadow-ell", cx: 150, cy: 284, rx: 74, ry: 9, style: "fill:var(--sph-shadow)", filter: "url(#speck)" }, svg);

  const float = svgEl("g", { class: "ball-float" }, svg);
  const spin = svgEl("g", { class: "ball-spin" }, float);
  sphere(spin, 150, 150, 100, (g) => {
    // Seams in the team color, curved a little so they wrap around the sphere.
    const seams = svgEl("g", { "clip-path": "url(#ballclip)", fill: "none", "stroke-linecap": "round" }, g);
    ["M150 40 Q156 150 150 260", "M40 150 Q150 192 260 150", "M80 66 Q132 156 84 236", "M220 66 Q176 156 214 236"].forEach((d) => {
      svgEl("path", { d: d, style: "stroke:var(--sph-lo)", "stroke-width": 11, opacity: 0.8 }, seams);
      svgEl("path", { d: d, style: "stroke:var(--team)", "stroke-width": 6 }, seams);
    });
  });
  svgEl("circle", { cx: 150, cy: 150, r: 98.5, fill: "none", style: "stroke:var(--team)", "stroke-width": 2, opacity: 0.55 }, spin);

  // Flecks: small slivers drifting around the ball.
  [[44, 96, -30, 16, 4, 0], [262, 78, 24, 18, 4, 1], [28, 190, 18, 12, 3, 1], [272, 170, -40, 14, 3, 0],
   [64, 40, 40, 10, 3, 1], [238, 238, -20, 12, 3, 0]].forEach((f) => {
    const g = svgEl("g", { transform: "translate(" + f[0] + " " + f[1] + ") rotate(" + f[2] + ")" }, svg);
    svgEl("ellipse", { class: "fleck", cx: 0, cy: 0, rx: f[3], ry: f[4], style: "fill:" + (f[5] ? "var(--team)" : "var(--ink)") }, g);
  });
}

function renderHomeText() {
  const team = selected();
  $("home-name").textContent = team.name;
  const sub = $("home-sub");
  sub.textContent = "";
  const status = team.status;
  if (status === "champion") {
    sub.appendChild(el("b", "", "Champion"));
  } else if (status === "eliminated") {
    sub.appendChild(el("b", "", "Eliminated"));
  } else {
    sub.appendChild(el("b", "", team.rank === 1 ? "Favorite" : "Rank " + team.rank));
    sub.appendChild(document.createTextNode(" · " + team.record + " · "));
    const change = formatChange(team.prob_change_1d);
    sub.appendChild(el("span", change.cls, change.text));
  }
}

function buildStrip() {
  const strip = $("home-strip");
  strip.textContent = "";
  const top = state.teams[0].title_prob;
  state.teams.slice(0, 8).forEach((team) => {
    const cell = el("button", "cell");
    cell.type = "button";
    cell.dataset.team = team.id;
    cell.setAttribute("aria-label", team.name + ", " + fmtPct1(team.title_prob));
    cell.style.setProperty("--w", String(Math.max(4, (team.title_prob / Math.max(top, 1e-9)) * 100)));
    cell.appendChild(el("span", "abbr", team.id));
    cell.appendChild(el("span", "nm", team.name));
    cell.appendChild(crest(team, 30));
    cell.appendChild(el("span", "pct", fmtPct0(team.title_prob)));
    const change = formatChange(team.prob_change_1d);
    cell.appendChild(el("span", "chg", change.text));
    cell.addEventListener("click", () => pick(team.id));
    strip.appendChild(cell);
  });
}

// ---------- Home: Table / Graph toggle ----------

function setHomeView(view) {
  state.homeView = view;
  const graphMode = view === "graph";
  $("page-home").classList.toggle("graph-mode", graphMode);
  $("home-strip").hidden = graphMode;
  $("home-graph").hidden = !graphMode;
  const wrap = $("home-view");
  wrap.textContent = "";
  [["table", "Table"], ["graph", "Graph"]].forEach((opt) => {
    const b = el("button", opt[0] === view ? "on" : "", opt[1]);
    b.type = "button";
    b.setAttribute("aria-pressed", String(opt[0] === view));
    b.addEventListener("click", () => { if (state.homeView !== opt[0]) setHomeView(opt[0]); });
    wrap.appendChild(b);
  });
  if (graphMode) renderGraph();
}

// "2026-11-08" -> "Nov 8" (parsed by hand so the time zone cannot shift the day).
function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return String(iso);
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString([], { month: "short", day: "numeric" });
}

// In the black theme a dark team color (navy) would vanish on the page, so it is lightened for lines.
function lineColor(hex) {
  if (document.documentElement.dataset.theme !== "black" || luminance(hex) > 0.12) return hex;
  const n = parseInt(hex.slice(1), 16);
  const mix = (c) => Math.round(c + (255 - c) * 0.55);
  return "#" + [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => mix(c).toString(16).padStart(2, "0")).join("");
}

const graphView = { idx: 0, hi: null, W: 0, H: 0, pts: null };

const GRAPH_TEAMS = 10;

// One color per team; when two of the ten colors are close (two reds, two blues), the later one gets a dash
// pattern so the lines stay tellable apart.
function lineStyles(teams) {
  const dashes = ["", "6 3", "2.5 3", "8 3 2 3"];
  const rgb = (hex) => { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
  const used = [];
  const styles = {};
  teams.forEach((team) => {
    const color = lineColor(colorsOf(team)[0]);
    const c = rgb(color);
    let k = 0;
    while (used.some((u) => u.k === k && dist(u.c, c) < 55)) k++;
    used.push({ c, k });
    styles[team.id] = { color, dash: dashes[k % dashes.length] };
  });
  return styles;
}

// Title odds over time for the top ten teams: thin lines in team colors plus a key underneath.
// Sized from the box it sits in so text stays sharp.
function renderGraph() {
  const box = $("home-graph");
  if (box.hidden) return;
  const dates = Array.isArray(state.data.history_dates) ? state.data.history_dates : [];
  const teams = state.teams.slice(0, GRAPH_TEAMS).filter((t) => Array.isArray(t.history) && t.history.length === dates.length && dates.length > 0);
  box.textContent = "";
  if (!teams.length) {
    box.appendChild(el("p", "trend-line", "No history in the data yet."));
    return;
  }
  const n = dates.length;
  box.classList.toggle("single", n < 2);
  const styles = lineStyles(teams);
  graphView.idx = n - 1;
  graphView.hi = state.selectedId;

  const read = el("div", "graph-read");
  read.id = "graph-read";
  read.setAttribute("aria-live", "polite");
  ["gr-date", "gr-team", "gr-val"].forEach((c) => read.appendChild(el("span", c)));
  box.appendChild(read);
  const wrap = el("div", "graph-svg");
  box.appendChild(wrap);
  if (n < 2) box.appendChild(el("p", "trend-line graph-note", "Trend builds daily · " + n + " day so far. Today's odds are shown."));

  // The key: one button per line (color + dash swatch and abbreviation); picking one selects that team.
  const key = el("div", "graph-key");
  key.setAttribute("role", "group");
  key.setAttribute("aria-label", "Key: tap a team to highlight its line");
  teams.forEach((team) => {
    const b = el("button", "gkey");
    b.type = "button";
    b.dataset.team = team.id;
    b.setAttribute("aria-label", team.name);
    b.setAttribute("aria-pressed", "false");
    const sw = svgEl("svg", { viewBox: "0 0 22 8", width: 22, height: 8, "aria-hidden": "true", focusable: "false" }, b);
    const att = { x1: 1, x2: 21, y1: 4, y2: 4, stroke: styles[team.id].color, "stroke-width": 2.4, "stroke-linecap": "round" };
    if (styles[team.id].dash) att["stroke-dasharray"] = styles[team.id].dash;
    svgEl("line", att, sw);
    b.appendChild(el("span", "", team.id));
    b.addEventListener("click", () => select(team.id));
    b.addEventListener("pointerenter", () => { graphView.hi = team.id; updateGraphReadout(); });
    b.addEventListener("pointerleave", () => { graphView.hi = state.selectedId; updateGraphReadout(); });
    key.appendChild(b);
  });
  box.appendChild(key);

  const W = Math.max(240, Math.round(wrap.clientWidth || 420));
  const H = Math.max(120, Math.round(wrap.clientHeight || 300));
  graphView.W = W;
  graphView.H = H;
  const L = 34, R = 10, T = 10, B = 24;
  const hiVal = Math.max(...teams.map((t) => Math.max(...t.history)), 0.01);
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25];
  const step = steps.find((v) => hiVal / v <= 4.5) || 0.25;
  const top = Math.ceil(hiVal / step) * step;
  const px = (i) => (n === 1 ? L + (W - L - R) / 2 : L + (i / (n - 1)) * (W - L - R));
  const py = (v) => H - B - (v / top) * (H - T - B);
  graphView.pts = { px, n };

  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "group", "aria-label": "Title odds over time for the top " + teams.length + " teams" }, wrap);
  for (let v = 0; v <= top + 1e-9; v += step) {
    svgEl("line", { class: "gridl", x1: L, x2: W - R, y1: py(v), y2: py(v) }, svg);
    const t = svgEl("text", { class: "gaxis", x: L - 6, y: py(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = Math.round(v * 100) + "%";
  }
  [0, n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => {
    const t = svgEl("text", { class: "gaxis", x: px(i), y: H - 6, "text-anchor": i === 0 && n > 1 ? "start" : n === 1 ? "middle" : "end" }, svg);
    t.textContent = shortDate(dates[i]);
  });
  const guide = svgEl("line", { class: "guide", x1: 0, x2: 0, y1: T, y2: H - B }, svg);
  guide.style.display = "none";

  const dots = {};
  teams.slice().reverse().forEach((team) => {
    const st = styles[team.id];
    const g = svgEl("g", { class: "gline", "data-team": team.id, tabindex: 0, role: "button", "aria-label": team.name + " title odds, " + fmtPct1(team.history[n - 1]) + " on " + shortDate(dates[n - 1]) + ". Arrow keys move through days." }, svg);
    if (n > 1) {
      const d = "M" + team.history.map((v, i) => px(i).toFixed(1) + " " + py(v).toFixed(1)).join(" L");
      const att = { class: "ln", d: d, fill: "none", stroke: st.color };
      if (st.dash) att["stroke-dasharray"] = st.dash;
      svgEl("path", att, g);
      svgEl("path", { d: d, fill: "none", stroke: "transparent", "stroke-width": 12 }, g);
    }
    dots[team.id] = svgEl("circle", { class: "gdot", cx: px(n - 1), cy: py(team.history[n - 1]), r: n === 1 ? 5.5 : 3.8, fill: st.color }, g);
    g.addEventListener("click", () => pick(team.id));
    g.addEventListener("focus", () => { graphView.hi = team.id; updateGraphReadout(); });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(team.id); return; }
      const move = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
      if (move) { e.preventDefault(); graphView.idx = Math.min(n - 1, Math.max(0, graphView.idx + move)); updateGraphReadout(); }
      if (e.key === "Home") { e.preventDefault(); graphView.idx = 0; updateGraphReadout(); }
      if (e.key === "End") { e.preventDefault(); graphView.idx = n - 1; updateGraphReadout(); }
    });
    g.addEventListener("pointerenter", () => { graphView.hi = team.id; updateGraphReadout(); });
  });
  graphView.dots = dots;
  graphView.guide = guide;
  graphView.teams = teams;
  graphView.dates = dates;
  graphView.py = py;
  graphView.svg = svg;

  // Pointer anywhere on the chart picks the day.
  svg.addEventListener("pointermove", (e) => {
    if (n < 2) return;
    const rect = svg.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    graphView.idx = Math.min(n - 1, Math.max(0, Math.round(((x - L) / (W - L - R)) * (n - 1))));
    updateGraphReadout();
  });
  svg.addEventListener("pointerleave", () => { graphView.idx = n - 1; graphView.hi = state.selectedId; updateGraphReadout(); });

  // The same numbers as a table for screen readers.
  const table = el("table", "sr-only");
  table.appendChild(el("caption", "", "Title odds by day, top " + teams.length + " teams"));
  const head = el("tr");
  head.appendChild(el("th", "", "Team"));
  dates.forEach((d) => head.appendChild(el("th", "", shortDate(d))));
  table.appendChild(head);
  teams.forEach((t) => {
    const tr = el("tr");
    tr.appendChild(el("th", "", t.name));
    t.history.forEach((v) => tr.appendChild(el("td", "", fmtPct1(v))));
    table.appendChild(tr);
  });
  box.appendChild(table);

  syncSelectionClasses();
  syncGraphSelection();
  updateGraphReadout();
}

// The selected team's line is drawn last (on top) and the other lines dim, when it is one of the ten.
function syncGraphSelection() {
  const box = $("home-graph");
  if (!graphView.svg || !graphView.teams || !box) return;
  const inGraph = graphView.teams.some((t) => t.id === state.selectedId);
  box.classList.toggle("has-sel", inGraph);
  const line = graphView.svg.querySelector('.gline[data-team="' + state.selectedId + '"]');
  if (line && line !== graphView.svg.lastElementChild) graphView.svg.appendChild(line);
}

// Moves the guide line and the readout to the chosen day and team.
function updateGraphReadout() {
  const read = $("graph-read");
  if (!read || !graphView.teams || $("home-graph").hidden) return;
  const hiId = graphView.teams.some((t) => t.id === graphView.hi) ? graphView.hi : (graphView.teams.some((t) => t.id === state.selectedId) ? state.selectedId : graphView.teams[0].id);
  const team = graphView.teams.find((t) => t.id === hiId);
  const i = graphView.idx;
  const n = graphView.pts.n;
  read.querySelector(".gr-date").textContent = shortDate(graphView.dates[i]);
  read.querySelector(".gr-team").textContent = team.name;
  read.querySelector(".gr-val").textContent = fmtPct1(team.history[i]);
  graphView.guide.style.display = n > 1 && i !== n - 1 ? "" : "none";
  graphView.guide.setAttribute("x1", graphView.pts.px(i));
  graphView.guide.setAttribute("x2", graphView.pts.px(i));
  graphView.teams.forEach((t) => {
    const dot = graphView.dots[t.id];
    dot.setAttribute("cx", graphView.pts.px(i));
    dot.setAttribute("cy", graphView.py(t.history[i]));
    dot.parentNode.classList.toggle("hi", t.id === hiId);
  });
}

let graphResizeFrame = 0;
function watchGraphSize() {
  const box = $("home-graph");
  if (!window.ResizeObserver) return;
  let last = "";
  new ResizeObserver(() => {
    if (box.hidden) return;
    const key = box.clientWidth + "x" + box.clientHeight;
    if (key === last) return;
    last = key;
    cancelAnimationFrame(graphResizeFrame);
    graphResizeFrame = requestAnimationFrame(renderGraph);
  }).observe(box);
}

// ---------- Page 2: Rankings ----------

// Greedy circle layout: biggest at the center, the rest as close as they fit, kept wide and flat.
function layoutBubbles(items) {
  const placed = [];
  items.forEach((item, i) => {
    if (i === 0) { placed.push({ item, x: 0, y: 0, r: item.r }); return; }
    for (let dist = (placed[0].r + item.r) * 0.7; dist < 600; dist += 3) {
      let best = null;
      for (let a = 0; a < 360; a += 12) {
        const ang = ((a + i * 47) * Math.PI) / 180;
        const x = Math.cos(ang) * dist;
        const y = Math.sin(ang) * dist;
        const fits = placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + item.r - 0.14 * Math.min(p.r, item.r));
        if (!fits) continue;
        const score = Math.pow(y * 1.7, 2) + x * x;
        if (!best || score < best.score) best = { x, y, score };
      }
      if (best) { placed.push({ item, x: best.x, y: best.y, r: item.r }); return; }
    }
  });
  return placed;
}

function buildBubbles() {
  const box = $("bubbles");
  box.textContent = "";
  const items = state.teams.slice(0, 6).map((team) => ({ team, r: 16 + 100 * Math.sqrt(Math.max(team.title_prob, 0)) }));
  const placed = layoutBubbles(items);
  const minX = Math.min(...placed.map((p) => p.x - p.r)) - 6;
  const maxX = Math.max(...placed.map((p) => p.x + p.r)) + 6;
  const minY = Math.min(...placed.map((p) => p.y - p.r)) - 6;
  const maxY = Math.max(...placed.map((p) => p.y + p.r)) + 6;
  const svg = svgEl("svg", { viewBox: [minX, minY, maxX - minX, maxY - minY].join(" "), role: "group", "aria-label": "Top six contenders, sized by title odds" }, box);
  placed.forEach((p) => {
    const team = p.item.team;
    const g = svgEl("g", { class: "bubble", tabindex: 0, role: "button", "data-team": team.id, "aria-label": team.name + ", " + fmtPct1(team.title_prob) }, svg);
    sphere(g, p.x, p.y, p.r, (surface) => {
      svgEl("circle", { cx: p.x, cy: p.y, r: p.r - 5, fill: "none", stroke: colorsOf(team)[0], "stroke-width": 2.5, opacity: 0.9 }, surface);
    });
    const abbr = svgEl("text", { x: p.x, y: p.y + p.r * 0.1, "text-anchor": "middle", "font-size": p.r * 0.52 }, g);
    abbr.textContent = team.id;
    const pct = svgEl("text", { x: p.x, y: p.y + p.r * 0.45, "text-anchor": "middle", "font-size": p.r * 0.27, style: "font-family:var(--mono)" }, g);
    pct.textContent = fmtPct1(team.title_prob);
    g.addEventListener("click", () => pick(team.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(team.id); } });
  });
}

function buildRankList() {
  const list = $("rank-list");
  list.textContent = "";
  state.teams.forEach((team, i) => {
    const out = team.status === "eliminated";
    const btn = el("button", "row" + (out ? " out" : ""));
    btn.type = "button";
    btn.dataset.team = team.id;
    btn.appendChild(el("span", "rk", String(i + 1)));
    btn.appendChild(crest(team, 22));
    btn.appendChild(el("span", "nm", team.name));
    btn.appendChild(el("span", "lead"));
    btn.appendChild(el("span", "val", out ? "OUT" : fmtPct1(team.title_prob)));
    btn.addEventListener("click", () => pick(team.id));
    list.appendChild(btn);
  });
  const title = document.querySelector("#page-rankings .page-title");
  if (!document.querySelector("#page-rankings .page-sub")) {
    const hint = el("p", "page-sub", "Select a team. Select it again for its details.");
    title.after(hint);
  }
}

// ---------- Page 3: Team ----------

const inputValue = (key) => (team) => {
  const input = team.inputs && team.inputs[key];
  return input && typeof input.value === "number" ? input.value : null;
};
const simValue = (key) => (team) => (team.sim && typeof team.sim[key] === "number" ? team.sim[key] : null);

const METRICS = [
  { key: "title_prob", label: "Title odds", get: (t) => t.title_prob, fmt: fmtPct1 },
  { key: "finals", label: "Reach Finals", get: simValue("finals"), fmt: fmtPct0 },
  { key: "conf_finals", label: "Conf. finals", get: simValue("conf_finals"), fmt: fmtPct0 },
  { key: "make_playoffs", label: "Make playoffs", get: simValue("make_playoffs"), fmt: fmtPct0 },
  { key: "net_rating", label: "Net rating", get: inputValue("net_rating"), fmt: fmtSigned1 },
  { key: "star_power", label: "Star power", get: inputValue("star_power"), fmt: fmtSigned1 },
  { key: "recent_form", label: "Recent form", get: inputValue("recent_form"), fmt: fmtSigned1 },
  { key: "star_availability", label: "Star avail.", get: inputValue("star_availability"), fmt: fmtPct0 },
  { key: "core_net_rating", label: "Core net rating", get: inputValue("core_net_rating"), fmt: fmtSigned1 },
  { key: "rotation_availability", label: "Rotation avail.", get: inputValue("rotation_availability"), fmt: fmtPct0 },
  { key: "clutch", label: "Clutch", get: inputValue("clutch"), fmt: fmtPct0 },
];

// A metric is shown only when at least one team has a number for it.
const activeMetrics = () => METRICS.filter((m) => state.teams.some((t) => m.get(t) !== null));
const currentMetric = () => METRICS.find((m) => m.key === state.metric) || METRICS[0];

function renderTeam() {
  const team = selected();
  if (!team) return;
  $("team-crest").textContent = "";
  $("team-crest").appendChild(crest(team, 120));
  updateBig($("team-big"), team.title_prob, state.page === PAGES.indexOf("team") ? false : true);
  $("team-name").textContent = team.name;
  const bits = [team.conference, team.record, team.status === "eliminated" ? "Eliminated" : team.status === "champion" ? "Champion" : "Rank " + team.rank];
  $("team-sub").textContent = bits.filter(Boolean).join(" · ");

  const rows = $("team-rows");
  rows.textContent = "";
  activeMetrics().forEach((m) => {
    const value = m.get(team);
    const btn = row(m.label, value === null ? "—" : m.fmt(value), { tag: "button", cls: m.key === state.metric ? "sel" : "" });
    btn.type = "button";
    btn.dataset.metric = m.key;
    btn.setAttribute("aria-pressed", String(m.key === state.metric));
    btn.addEventListener("click", () => { state.metric = m.key; renderTeamMetric(); });
    rows.appendChild(btn);
  });
  $("team-notes").textContent = (team.notes || []).join(" · ");
  renderLeague();
  renderTrend();
}

function renderTeamMetric() {
  document.querySelectorAll("#team-rows .row").forEach((btn) => {
    const on = btn.dataset.metric === state.metric;
    btn.classList.toggle("sel", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  renderLeague();
}

// Thirty thin bars: where the selected team sits among all teams for the chosen row.
function renderLeague() {
  const box = $("league");
  box.textContent = "";
  const metric = currentMetric();
  const rows = state.teams.map((t) => ({ t, v: metric.get(t) })).filter((r) => r.v !== null).sort((a, b) => b.v - a.v);
  if (!rows.length) return;
  const max = rows[0].v;
  const min = rows[rows.length - 1].v;
  const bars = el("div", "league-bars");
  bars.setAttribute("role", "group");
  bars.setAttribute("aria-label", metric.label + " across all teams");
  rows.forEach((r) => {
    const bar = el("button", "lbar" + (r.t.id === state.selectedId ? " sel" : ""));
    bar.type = "button";
    bar.dataset.team = r.t.id;
    bar.setAttribute("aria-label", r.t.name + ", " + metric.fmt(r.v));
    bar.title = r.t.id + " " + metric.fmt(r.v);
    const fill = el("i");
    fill.style.height = (max === min ? 60 : 8 + ((r.v - min) / (max - min)) * 92) + "%";
    bar.appendChild(fill);
    bar.addEventListener("click", () => select(r.t.id));
    bars.appendChild(bar);
  });
  box.appendChild(bars);
  const cap = el("div", "league-cap");
  cap.appendChild(el("span", "", metric.label));
  cap.appendChild(el("span", "", "High → low, " + rows.length + " teams"));
  box.appendChild(cap);
}

function renderTrend() {
  const box = $("trend");
  box.textContent = "";
  const team = selected();
  const dates = state.data.history_dates;
  const hist = team.history;
  if (!Array.isArray(dates) || !Array.isArray(hist) || hist.length < 3 || dates.length !== hist.length) {
    const n = Array.isArray(hist) ? hist.length : 0;
    box.appendChild(el("p", "trend-line", "History builds daily · " + n + (n === 1 ? " day" : " days") + " so far"));
    return;
  }
  const lo = Math.min(...hist);
  const hi = Math.max(...hist);
  const span = hi - lo || 1;
  const pts = hist.map((v, i) => [(i / (hist.length - 1)) * 300, 56 - ((v - lo) / span) * 50]);
  const read = el("div", "trend-read");
  const dateEl = el("span", "", "");
  const valEl = el("span", "", "");
  read.appendChild(dateEl);
  read.appendChild(valEl);
  box.appendChild(read);
  const svg = svgEl("svg", { viewBox: "0 0 300 62", preserveAspectRatio: "none", "aria-hidden": "true" }, box);
  svgEl("path", { d: "M" + pts.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L"), fill: "none", stroke: "currentColor", "stroke-width": 2.5, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }, svg);
  const dot = svgEl("circle", { r: 4.5, style: "fill:var(--team);stroke:var(--ink);stroke-width:2" }, svg);
  const slider = el("input", "scrub");
  slider.type = "range";
  slider.min = 0;
  slider.max = hist.length - 1;
  slider.value = hist.length - 1;
  slider.setAttribute("aria-label", "Scrub through the history of " + team.name + " title odds");
  const paint = () => {
    const i = Number(slider.value);
    dot.setAttribute("cx", pts[i][0]);
    dot.setAttribute("cy", pts[i][1]);
    dateEl.textContent = dates[i];
    valEl.textContent = fmtPct1(hist[i]);
  };
  slider.addEventListener("input", paint);
  paint();
  box.appendChild(slider);
}

// ---------- Page 4: Map ----------

function buildMap() {
  const box = $("map");
  box.textContent = "";
  const pts = state.teams.filter((t) => inputValue("net_rating")(t) !== null && inputValue("star_power")(t) !== null);
  if (pts.length < 2) {
    box.appendChild(el("p", "trend-line", "Not enough data for the map yet."));
    return;
  }
  const W = 360, H = 400, L = 26, R = 14, T = 12, B = 30;
  const xs = pts.map(inputValue("net_rating"));
  const ys = pts.map(inputValue("star_power"));
  const pad = (a, b) => [Math.min(...a) - (Math.max(...a) - Math.min(...a)) * 0.1 - 0.5, Math.max(...a) + (Math.max(...a) - Math.min(...a)) * 0.1 + 0.5];
  const [x0, x1] = pad(xs);
  const [y0, y1] = pad(ys);
  const px = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
  const py = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);

  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": "Net rating against star power for every team" }, box);
  if (x0 < 0 && x1 > 0) svgEl("line", { class: "axis", x1: px(0), x2: px(0), y1: T, y2: H - B }, svg);
  if (y0 < 0 && y1 > 0) svgEl("line", { class: "axis", x1: L, x2: W - R, y1: py(0), y2: py(0) }, svg);
  svgEl("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "none", class: "axis" }, svg);
  const xl = svgEl("text", { x: W - R, y: H - 8, "text-anchor": "end" }, svg);
  xl.textContent = "Net rating →";
  const yl = svgEl("text", { x: L + 4, y: T + 11 }, svg);
  yl.textContent = "↑ Star power";

  pts.slice().sort((a, b) => b.title_prob - a.title_prob).forEach((team) => {
    const r = 4 + 32 * Math.sqrt(Math.max(team.title_prob, 0));
    const g = svgEl("g", { class: "dotb", "data-team": team.id, tabindex: 0, role: "button", "aria-label": team.name }, svg);
    const cx = px(inputValue("net_rating")(team));
    const cy = py(inputValue("star_power")(team));
    svgEl("circle", { cx: cx, cy: cy, r: r, fill: colorsOf(team)[0] }, g);
    svgEl("circle", { class: "dshade", cx: cx, cy: cy, r: r, fill: "url(#sph-shade)", "pointer-events": "none" }, g);
    svgEl("circle", { class: "dshade", cx: cx - r * 0.34, cy: cy - r * 0.4, r: r * 0.3, fill: "url(#sph-spec)", "pointer-events": "none" }, g);
    if (r >= 12) {
      const label = svgEl("text", { x: px(inputValue("net_rating")(team)), y: py(inputValue("star_power")(team)) + 3, style: "fill:" + (luminance(colorsOf(team)[0]) > 0.45 ? "#111" : "#fff") }, g);
      label.textContent = team.id;
    }
    g.addEventListener("click", () => select(team.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(team.id); } });
  });
  svgEl("text", { class: "lbl", id: "map-label", "text-anchor": "middle" }, svg);

  // Same numbers as a table for screen readers.
  const table = $("map-table");
  table.textContent = "";
  const head = el("tr");
  ["Team", "Net rating", "Star power", "Title odds"].forEach((h) => head.appendChild(el("th", "", h)));
  table.appendChild(head);
  state.teams.forEach((team) => {
    const tr = el("tr");
    const n = inputValue("net_rating")(team);
    const s = inputValue("star_power")(team);
    [team.name, n === null ? "n/a" : fmtSigned1(n), s === null ? "n/a" : fmtSigned1(s), fmtPct1(team.title_prob)].forEach((v) => tr.appendChild(el("td", "", v)));
    table.appendChild(tr);
  });
  box._scale = { px, py };
  renderMapSelection();
}

function renderMapSelection() {
  const box = $("map");
  const team = selected();
  if (!box._scale || !team) return;
  box.querySelectorAll(".dotb").forEach((g) => g.classList.toggle("sel", g.dataset.team === team.id));
  const net = inputValue("net_rating")(team);
  const star = inputValue("star_power")(team);
  const label = $("map-label");
  if (label && net !== null && star !== null) {
    const x = box._scale.px(net);
    label.textContent = team.id;
    label.setAttribute("x", Math.min(Math.max(x, 40), 330));
    label.setAttribute("y", box._scale.py(star) - 4 - 32 * Math.sqrt(Math.max(team.title_prob, 0)));
  }
  const out = $("map-readout");
  out.textContent = "";
  out.appendChild(row(team.name, fmtPct1(team.title_prob), { cls: "sel static" }));
  out.appendChild(row("Net rating", net === null ? "—" : fmtSigned1(net), { cls: "static" }));
  out.appendChild(row("Star power", star === null ? "—" : fmtSigned1(star), { cls: "static" }));
}

// ---------- Page 5: Model ----------

const WEIGHT_LABELS = {
  net_rating: "Net rating", star_power: "Star power", recent_form: "Recent form",
  star_availability: "Star availability", rotation_availability: "Rotation avail.",
  core_net_rating: "Core net rating", clutch: "Clutch",
};

function buildModel() {
  const weights = Object.keys(state.data.weights || {})
    .map((k) => ({ key: k, w: state.data.weights[k] }))
    .filter((x) => typeof x.w === "number" && x.w > 0)
    .sort((a, b) => b.w - a.w);

  const rows = $("model-rows");
  rows.textContent = "";
  weights.forEach((x, i) => rows.appendChild(row(WEIGHT_LABELS[x.key] || x.key, Math.round(x.w * 100) + "%", { cls: "static" + (i === 0 ? " sel" : "") })));

  // Pucks: one floating disc per input, as wide as its weight. Lit top, shaded side, thickness below.
  const box = $("slabs");
  box.textContent = "";
  if (weights.length) {
    const step = 27, ry = 7, th = 9;
    const svg = svgEl("svg", { viewBox: "0 0 360 " + (weights.length * step + 14), "aria-hidden": "true", preserveAspectRatio: "xMidYMid meet" }, box);
    const maxW = weights[0].w;
    weights.forEach((x, i) => {
      const rx = (90 + 220 * (x.w / maxW)) / 2;
      const cy = 12 + i * step;
      const g = svgEl("g", { class: "slab" }, svg);
      svgEl("ellipse", { cx: 180, cy: cy + th, rx: rx, ry: ry, fill: "url(#puck-side)", filter: "url(#speck)" }, g);
      svgEl("rect", { x: 180 - rx, y: cy, width: rx * 2, height: th, fill: "url(#puck-side)", filter: "url(#speck)" }, g);
      svgEl("ellipse", { cx: 180, cy: cy, rx: rx, ry: ry, fill: "url(#sph-base)", filter: "url(#speck)" }, g);
      svgEl("ellipse", { cx: 180 - rx * 0.28, cy: cy - ry * 0.25, rx: rx * 0.4, ry: ry * 0.42, fill: "url(#sph-spec)", opacity: 0.7 }, g);
    });
  }

  const d = new Date(state.data.generated_at);
  const parts = [];
  if (!Number.isNaN(d.getTime())) parts.push("Updated " + d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }));
  if (state.data.model_version) parts.push("Model " + state.data.model_version);
  if (state.data.sim && state.data.sim.runs) parts.push(state.data.sim.runs.toLocaleString() + " simulations");
  if (state.data.phase) parts.push("Phase: " + String(state.data.phase).replace("_", " "));
  $("model-fine").textContent = parts.join(" · ");
}

// ---------- Header, status ----------

function renderHeader() {
  $("season-label").textContent = state.data.season + " champion";
  $("sample-badge").hidden = state.data.is_sample !== true;
  const updated = new Date(state.data.generated_at);
  if (!Number.isNaN(updated.getTime())) {
    $("stale-notice").hidden = (Date.now() - updated.getTime()) / 36e5 <= STALE_AFTER_HOURS;
  }
}

function showError(message) {
  $("loading").hidden = true;
  $("track").hidden = true;
  $("dots").hidden = true;
  $("topnav").hidden = true;
  const box = $("error");
  box.textContent = message;
  box.hidden = false;
}

// ---------- Pager ----------

const track = $("track");
const DESKTOP = window.matchMedia("(min-width: 900px)");

function go(index, fromHash) {
  state.page = Math.max(0, Math.min(PAGES.length - 1, index));
  track.style.transform = "translateX(" + -state.page * 100 + "%)";
  document.querySelectorAll(".page").forEach((page, i) => {
    page.inert = i !== state.page;
    page.setAttribute("aria-hidden", String(i !== state.page));
    page.classList.toggle("before", i < state.page);
  });
  document.querySelectorAll(".navlink").forEach((link, i) => {
    link.classList.toggle("on", i === state.page);
    link.setAttribute("aria-current", i === state.page ? "page" : "false");
  });
  document.querySelectorAll(".dot").forEach((dot, i) => {
    dot.classList.toggle("on", i === state.page);
    dot.setAttribute("aria-current", i === state.page ? "page" : "false");
  });
  if (!fromHash) history.replaceState(null, "", "#" + PAGES[state.page]);
  if (state.data && state.selectedId) {
    const team = selected();
    if (PAGES[state.page] === "home") updateBig($("home-big"), team.title_prob, true);
    if (PAGES[state.page] === "team") updateBig($("team-big"), team.title_prob, true);
  }
}

function buildDots() {
  const nav = $("dots");
  nav.textContent = "";
  PAGES.forEach((id, i) => {
    const dot = el("button", "dot");
    dot.type = "button";
    dot.setAttribute("aria-label", document.getElementById("page-" + id).dataset.title + " page");
    dot.addEventListener("click", () => go(i));
    nav.appendChild(dot);
  });
  // Desktop: the same pages as a row of named links in the top bar.
  const bar = $("topnav");
  bar.textContent = "";
  PAGES.forEach((id, i) => {
    const link = el("button", "navlink", document.getElementById("page-" + id).dataset.title);
    link.type = "button";
    link.addEventListener("click", () => go(i));
    bar.appendChild(link);
  });
}

function setupPager() {
  let start = null;
  let dragging = false;
  let suppressClick = false;

  track.addEventListener("pointerdown", (e) => {
    if (e.target.closest("input[type=range]")) return;
    if (DESKTOP.matches && e.pointerType === "mouse") return; // on desktop the mouse uses the top nav, not drag
    start = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
    dragging = false;
  });
  track.addEventListener("pointermove", (e) => {
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (!dragging) {
      if (Math.abs(dx) < 10 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      dragging = true;
      track.classList.add("dragging");
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* capture is optional */ }
    }
    const atEdge = (dx > 0 && state.page === 0) || (dx < 0 && state.page === PAGES.length - 1);
    track.style.transform = "translateX(calc(" + -state.page * 100 + "% + " + (atEdge ? dx * 0.3 : dx) + "px))";
  });
  const end = (e, cancelled) => {
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x;
    const speed = Math.abs(dx) / Math.max(1, performance.now() - start.t);
    const width = track.clientWidth;
    const wasDragging = dragging;
    start = null;
    dragging = false;
    track.classList.remove("dragging");
    if (!wasDragging) return;
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 0);
    if (!cancelled && (Math.abs(dx) > width * 0.18 || speed > 0.5)) go(state.page + (dx < 0 ? 1 : -1));
    else go(state.page);
  };
  track.addEventListener("pointerup", (e) => end(e, false));
  track.addEventListener("pointercancel", (e) => end(e, true));
  track.addEventListener("click", (e) => { if (suppressClick) { e.stopPropagation(); e.preventDefault(); } }, true);

  let wheelLock = 0;
  track.addEventListener("wheel", (e) => {
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY) || Math.abs(e.deltaX) < 20) return;
    e.preventDefault();
    const now = performance.now();
    if (now - wheelLock < 700) return;
    wheelLock = now;
    go(state.page + (e.deltaX > 0 ? 1 : -1));
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches && e.target.matches("input")) return;
    if (e.key === "ArrowRight") go(state.page + 1);
    if (e.key === "ArrowLeft") go(state.page - 1);
  });

  window.addEventListener("hashchange", () => {
    const i = PAGES.indexOf(location.hash.slice(1));
    if (i >= 0) go(i, true);
  });
}

// ---------- Theme ----------

function setupTheme() {
  const forced = new URLSearchParams(location.search).get("theme");
  if (forced === "amber" || forced === "black") document.documentElement.dataset.theme = forced;
  const sync = () => {
    const meta = document.querySelector("meta[name=theme-color]");
    if (meta) meta.content = document.documentElement.dataset.theme === "black" ? "#1a1a1a" : "#ffa800";
  };
  sync();
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "black" ? "amber" : "black";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("champ-theme", next); } catch (e) { /* private mode */ }
    sync();
    if (state.data && !$("home-graph").hidden) renderGraph(); // line colors depend on the theme
  });
}

// ---------- Start ----------

async function init() {
  setupTheme();
  let data;
  try {
    const response = await fetch("data/odds.json", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    data = await response.json();
    if (!data || !Array.isArray(data.teams) || data.teams.length === 0) throw new Error("no teams in the file");
  } catch (err) {
    showError("Could not load the odds (" + err.message + "). Try again in a few minutes.");
    return;
  }

  state.data = data;
  state.teams = data.teams.slice().sort((a, b) => b.title_prob - a.title_prob || a.rank - b.rank);
  renderHeader();

  buildDots();
  setupPager();
  buildBall();
  buildStrip();
  setHomeView("table");
  watchGraphSize();
  buildBubbles();
  buildRankList();
  buildMap();
  buildModel();
  setupBig($("home-big"));
  setupBig($("team-big"));

  $("loading").hidden = true;
  track.hidden = false;
  select(state.teams[0].id);
  const start = PAGES.indexOf(location.hash.slice(1));
  go(start >= 0 ? start : 0, true);
}

init();
