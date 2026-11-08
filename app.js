// Renders data/odds.json as a dashboard (SPEC.md section 8): headline tiles,
// tabbed charts, a table view, and a breakdown panel for the selected team.
// Plain script, no dependencies. Charts live in charts.js. All text goes in with
// textContent (never innerHTML), so nothing in the JSON can be interpreted as HTML.

const STALE_AFTER_HOURS = 48;

// ---------- Number formatting ----------

const fmtPct1 = (v) => (v * 100).toFixed(1) + "%";
const fmtPct0 = (v) => Math.round(v * 100) + "%";
const tickPct = (v) => Math.round(v * 100) + "%";

// Signed number with a real minus sign, e.g. +3.4 / −1.2 / 0.0.
function fmtSigned(v, digits) {
  const rounded = Number(v.toFixed(digits));
  const text = Math.abs(rounded).toFixed(digits);
  return rounded > 0 ? "+" + text : rounded < 0 ? "−" + text : text;
}
const fmtSigned1 = (v) => fmtSigned(v, 1);
const tickNumber = (v) => (v < 0 ? "−" : "") + String(Number(Math.abs(v).toFixed(2)));

// 1 -> "1st", 22 -> "22nd", 100 -> "100th"
function ordinal(n) {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return n + "th";
  return n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
}

// Change since yesterday, in percentage points, e.g. "▲ 0.4".
function formatChange(delta) {
  const points = Math.abs(delta * 100).toFixed(1);
  if (Number(points) === 0) return { text: "–", cls: "" };
  return delta > 0
    ? { text: "▲ " + points, cls: "up" }
    : { text: "▼ " + points, cls: "down" };
}

// ---------- What can be charted ----------

const inputValue = (key) => (team) => {
  const input = team.inputs && team.inputs[key];
  return input && typeof input.value === "number" ? input.value : null;
};

// One entry per chartable measure. `description` follows SPEC.md section 6.1.
const METRICS = {
  title_prob: {
    label: "Title probability", short: "Title %", get: (t) => t.title_prob, fmt: fmtPct1, tick: tickPct,
    description: "The model's estimated chance of winning the championship.",
  },
  net_rating: {
    label: "Net rating", short: "Net rating", get: inputValue("net_rating"), fmt: fmtSigned1, tick: tickNumber,
    description: "Points scored minus allowed per 100 possessions, adjusted for schedule.",
  },
  core_net_rating: {
    label: "Core net rating", short: "Core NRtg", get: inputValue("core_net_rating"), fmt: fmtSigned1, tick: tickNumber,
    description: "Net rating in games where both stars played.",
  },
  star_power: {
    label: "Star power", short: "Star power", get: inputValue("star_power"), fmt: fmtSigned1, tick: tickNumber,
    description: "Weighted BPM (box plus-minus) of the team's two best players.",
  },
  recent_form: {
    label: "Recent form", short: "Form", get: inputValue("recent_form"), fmt: fmtSigned1, tick: tickNumber,
    description: "Net rating over the team's last 20 games.",
  },
  star_availability: {
    label: "Star availability", short: "Star avail.", get: inputValue("star_availability"), fmt: fmtPct0, tick: tickPct,
    description: "Share of games the two stars have played, weighted over the last three seasons.",
  },
  rotation_availability: {
    label: "Rotation availability", short: "Rotation avail.", get: inputValue("rotation_availability"), fmt: fmtPct0, tick: tickPct,
    description: "Minutes-weighted share of games played by the top nine players.",
  },
  clutch: {
    label: "Clutch", short: "Clutch", get: inputValue("clutch"), fmt: fmtPct0, tick: tickPct,
    description: "Win % in close late-game situations. High values tend to regress toward average.",
  },
};

// Order of the model inputs in the team panel.
const INPUT_ORDER = [
  "net_rating", "star_power", "recent_form", "star_availability",
  "core_net_rating", "rotation_availability", "clutch",
];

// Tabs. A tab with two metrics gets a small switch next to the chart title.
const TABS = [
  { id: "odds", label: "Odds", kind: "bar", metrics: ["title_prob"] },
  { id: "net-rating", label: "Net rating", kind: "bar", metrics: ["net_rating", "core_net_rating"] },
  { id: "star-power", label: "Star power", kind: "bar", metrics: ["star_power"] },
  { id: "form", label: "Recent form", kind: "bar", metrics: ["recent_form"] },
  { id: "availability", label: "Availability", kind: "bar", metrics: ["star_availability", "rotation_availability"] },
  { id: "clutch", label: "Clutch", kind: "bar", metrics: ["clutch"] },
  { id: "contenders", label: "Contenders", kind: "scatter", metrics: [] },
];

// ---------- State ----------

const state = {
  data: null,
  teams: [],            // sorted by title probability, best first
  tabId: "odds",
  metricByTab: {},      // which metric is showing on tabs that have a switch
  view: "chart",        // "chart" or "table"
  selectedId: null,     // team highlighted everywhere; null = none
  chartWidth: 0,
};

// ---------- Small DOM helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const teamById = (id) => state.teams.find((t) => t.id === id) || null;
const currentTab = () => TABS.find((t) => t.id === state.tabId);
const currentMetricKey = () => {
  const tab = currentTab();
  return state.metricByTab[tab.id] || tab.metrics[0];
};

function weightPercent(key) {
  const w = state.data.weights && state.data.weights[key];
  return typeof w === "number" ? Math.round(w * 100) : null;
}

// A row of pill buttons (used for the metric switch and the chart/table switch).
function renderSegmented(container, options, activeId, onChange) {
  const hadFocus = container.contains(document.activeElement);
  const focusIndex = options.findIndex((o) => o.id === activeId);
  container.textContent = "";
  container.hidden = options.length < 2;
  options.forEach((option) => {
    const button = el("button", "segment" + (option.id === activeId ? " active" : ""), option.label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(option.id === activeId));
    button.addEventListener("click", () => onChange(option.id));
    container.appendChild(button);
  });
  if (hadFocus && focusIndex >= 0) container.children[focusIndex].focus();
}

// ---------- Header ----------

function showError(message) {
  document.getElementById("loading").hidden = true;
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
}

function renderHeader(data) {
  document.getElementById("season").textContent = data.season + " season";
  document.getElementById("sample-badge").hidden = data.is_sample !== true;
  const updated = new Date(data.generated_at);
  if (Number.isNaN(updated.getTime())) return;
  document.getElementById("updated").textContent =
    "Last updated " + updated.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const ageHours = (Date.now() - updated.getTime()) / 36e5;
  document.getElementById("stale-notice").hidden = ageHours <= STALE_AFTER_HOURS;
}

// ---------- Headline tiles ----------

function tile(label, value, sub, isHero) {
  const box = el("div", "tile" + (isHero ? " tile-hero" : ""));
  box.appendChild(el("div", "tile-label", label));
  box.appendChild(el("div", "tile-value", value));
  if (sub) box.appendChild(el("div", "tile-sub", sub));
  return box;
}

function renderKpis() {
  const box = document.getElementById("kpis");
  box.textContent = "";
  const [first] = state.teams;
  const top3 = state.teams.slice(0, 3).reduce((sum, t) => sum + t.title_prob, 0);
  const contenders = state.teams.filter((t) => t.title_prob >= 0.05).length;
  const change = formatChange(first.prob_change_1d || 0);

  const hero = tile("Favorite", fmtPct1(first.title_prob), first.name, true);
  const changeEl = el("span", "change " + change.cls, change.text);
  changeEl.title = "Change since yesterday, in percentage points";
  hero.lastChild.appendChild(changeEl);
  box.appendChild(hero);
  box.appendChild(tile("Top 3 combined", fmtPct1(top3), state.teams.slice(0, 3).map((t) => t.id).join(" · ")));
  box.appendChild(tile("Teams above 5%", String(contenders), "of " + state.teams.length + " teams"));
}

// ---------- Tabs ----------

function renderTabs() {
  const bar = document.getElementById("tabs");
  TABS.forEach((tab) => {
    const button = el("button", "tab", tab.label);
    button.type = "button";
    button.id = "tab-" + tab.id;
    button.setAttribute("role", "tab");
    button.addEventListener("click", () => selectTab(tab.id));
    button.addEventListener("keydown", (e) => {
      const index = TABS.findIndex((t) => t.id === state.tabId);
      let next = null;
      if (e.key === "ArrowRight") next = TABS[(index + 1) % TABS.length];
      if (e.key === "ArrowLeft") next = TABS[(index - 1 + TABS.length) % TABS.length];
      if (next) {
        e.preventDefault();
        selectTab(next.id);
        document.getElementById("tab-" + next.id).focus();
      }
    });
    bar.appendChild(button);
  });
}

function updateTabs() {
  TABS.forEach((tab) => {
    const button = document.getElementById("tab-" + tab.id);
    const active = tab.id === state.tabId;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
}

function selectTab(id) {
  state.tabId = id;
  state.view = "chart";
  history.replaceState(null, "", "#" + id);
  Charts.hideTooltip();
  updateTabs();
  renderChartCard();
}

// ---------- Chart card ----------

function renderChartCard() {
  const tab = currentTab();
  const title = document.getElementById("chart-title");
  const sub = document.getElementById("chart-sub");
  const caption = document.getElementById("chart-caption");
  const body = document.getElementById("chart-body");
  body.setAttribute("role", "tabpanel");
  body.setAttribute("aria-labelledby", "tab-" + tab.id);

  if (tab.kind === "scatter") {
    title.textContent = "Contenders map";
    sub.textContent = "Net rating (how well a team plays) against star power (how good its two best players are). Teams to the upper right are strong on both.";
    caption.textContent = "Dot size shows title probability. Select a team to see its breakdown.";
  } else {
    const key = currentMetricKey();
    const metric = METRICS[key];
    const weight = weightPercent(key);
    title.textContent = metric.label + " by team";
    sub.textContent = metric.description + (weight !== null && key !== "title_prob" ? " Counts for " + weight + "% of the model score." : "");
    caption.textContent = key === "title_prob"
      ? "Bars are sorted by title probability. Select a team to see its breakdown."
      : "Sorted high to low. Select a team to see its breakdown.";
  }

  renderSegmented(
    document.getElementById("metric-switch"),
    tab.metrics.map((k) => ({ id: k, label: METRICS[k].label })),
    tab.kind === "bar" ? currentMetricKey() : null,
    (k) => { state.metricByTab[tab.id] = k; renderChartCard(); },
  );
  renderSegmented(
    document.getElementById("view-switch"),
    [{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }],
    state.view,
    (v) => { state.view = v; Charts.hideTooltip(); renderChartCard(); },
  );
  document.getElementById("chart-caption").hidden = state.view === "table";

  renderChartBody();
}

// Rows for the current bar metric: teams that have a value, highest first.
function barRows(metricKey) {
  const metric = METRICS[metricKey];
  return state.teams
    .map((t) => ({ id: t.id, name: t.name, value: metric.get(t), team: t }))
    .filter((r) => r.value !== null)
    .sort((a, b) => b.value - a.value);
}

function tooltipForBar(metricKey) {
  const metric = METRICS[metricKey];
  return (row) => {
    const input = row.team.inputs && row.team.inputs[metricKey];
    const parts = [row.team.record + " record"];
    if (input) parts.push(ordinal(input.percentile) + " percentile");
    if (metricKey === "title_prob") parts.push("rank " + row.team.rank);
    return { value: metric.fmt(row.value), name: row.name, detail: parts.join(" · ") };
  };
}

function renderChartBody() {
  const body = document.getElementById("chart-body");
  const tab = currentTab();
  state.chartWidth = body.clientWidth;

  if (state.view === "table") {
    renderTable(body, tab);
    return;
  }
  if (tab.kind === "scatter") {
    const points = state.teams
      .map((t) => ({ id: t.id, name: t.name, x: inputValue("net_rating")(t), y: inputValue("star_power")(t), size: t.title_prob, team: t }))
      .filter((p) => p.x !== null && p.y !== null);
    Charts.drawScatter(body, {
      points,
      xLabel: "Net rating", yLabel: "Star power",
      xTick: tickNumber, yTick: tickNumber,
      selectedId: state.selectedId,
      onSelect: selectTeam,
      ariaLabel: "Net rating against star power, dot size is title probability",
      labelCount: 5,
      tooltip: (p) => ({
        value: fmtPct1(p.size),
        name: p.name,
        detail: "Net rating " + fmtSigned1(p.x) + " · Star power " + fmtSigned1(p.y),
      }),
    });
    return;
  }
  const key = currentMetricKey();
  const metric = METRICS[key];
  Charts.drawBarChart(body, {
    rows: barRows(key),
    format: metric.fmt,
    tickFormat: metric.tick,
    selectedId: state.selectedId,
    onSelect: selectTeam,
    tooltip: tooltipForBar(key),
    ariaLabel: metric.label + " by team",
  });
}

// Table view: every number the chart shows (and a bit more), readable without hovering.
function renderTable(body, tab) {
  body.textContent = "";
  const wrap = el("div", "table-wrap");
  const table = el("table", "data-table");
  const columns = [];
  let rows;

  if (tab.kind === "scatter") {
    columns.push("#", "Team", "Net rating", "Star power", "Title %");
    rows = state.teams.map((t, i) => ({ team: t, cells: [
      String(i + 1), t.name,
      inputValue("net_rating")(t) === null ? "n/a" : fmtSigned1(inputValue("net_rating")(t)),
      inputValue("star_power")(t) === null ? "n/a" : fmtSigned1(inputValue("star_power")(t)),
      fmtPct1(t.title_prob),
    ] }));
  } else {
    const key = currentMetricKey();
    const metric = METRICS[key];
    const isOdds = key === "title_prob";
    columns.push("#", "Team", metric.label);
    columns.push(isOdds ? "Since yesterday" : "Percentile");
    rows = barRows(key).map((r, i) => {
      const input = r.team.inputs && r.team.inputs[key];
      const extra = isOdds
        ? formatChange(r.team.prob_change_1d || 0).text
        : input ? ordinal(input.percentile) : "n/a";
      return { team: r.team, cells: [String(i + 1), r.name, metric.fmt(r.value), extra] };
    });
  }

  const head = el("thead");
  const headRow = el("tr");
  columns.forEach((label, i) => headRow.appendChild(el("th", i < 2 ? "left" : "", label)));
  head.appendChild(headRow);
  table.appendChild(head);

  const tbody = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr", state.selectedId === row.team.id ? "selected" : "");
    tr.tabIndex = 0;
    tr.addEventListener("click", () => selectTeam(row.team.id));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTeam(row.team.id); }
    });
    row.cells.forEach((text, i) => tr.appendChild(el("td", i < 2 ? "left" : "", text)));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  body.appendChild(wrap);
}

// ---------- Team panel ----------

function selectTeam(id, scrollToPanel) {
  // Selecting the same team again clears the selection.
  state.selectedId = state.selectedId === id ? null : id;
  renderChartBody();
  renderDetail();
  const stacked = window.matchMedia("(max-width: 1000px)").matches;
  if (state.selectedId && stacked && scrollToPanel !== false) {
    document.getElementById("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function renderDetail() {
  const box = document.getElementById("detail");
  box.textContent = "";
  const team = teamById(state.selectedId) || state.teams[0];
  const isSelected = state.selectedId !== null;

  box.appendChild(el("p", "eyebrow", isSelected ? "Selected team" : "Current favorite"));
  box.appendChild(el("h2", "detail-name", team.name));
  box.appendChild(el("p", "detail-meta", team.conference + "ern Conference · " + team.record));

  const figures = el("div", "detail-figures");
  const prob = el("div", "detail-prob");
  prob.appendChild(el("span", "detail-prob-value", fmtPct1(team.title_prob)));
  prob.appendChild(el("span", "detail-prob-label", "title probability"));
  figures.appendChild(prob);
  const change = formatChange(team.prob_change_1d || 0);
  const side = el("div", "detail-side");
  side.appendChild(el("span", "detail-rank", "Rank " + team.rank + " of " + state.teams.length));
  const changeEl = el("span", "change " + change.cls, change.text + (change.text === "–" ? "" : " pts today"));
  side.appendChild(changeEl);
  figures.appendChild(side);
  box.appendChild(figures);

  box.appendChild(el("h3", "detail-heading", "What drives it"));
  const list = el("ul", "inputs");
  INPUT_ORDER.forEach((key) => {
    const input = team.inputs && team.inputs[key];
    const metric = METRICS[key];
    const item = el("li", "input-row");
    const top = el("div", "input-top");
    top.appendChild(el("span", "input-label", metric.label));
    top.appendChild(el("span", "input-value", input ? metric.fmt(input.value) : "n/a"));
    item.appendChild(top);

    const meter = el("div", "meter");
    if (input) {
      meter.setAttribute("role", "img");
      meter.setAttribute("aria-label", metric.label + ": " + ordinal(input.percentile) + " percentile");
      const fill = el("div", "meter-fill");
      fill.style.width = Math.max(0, Math.min(100, input.percentile)) + "%";
      meter.appendChild(fill);
    }
    item.appendChild(meter);

    const weight = weightPercent(key);
    const parts = [];
    if (input) parts.push(ordinal(input.percentile) + " percentile");
    if (weight !== null) parts.push(weight + "% weight");
    if (input && typeof input.contribution === "number") parts.push("adds " + fmtSigned(input.contribution, 2));
    item.appendChild(el("div", "input-sub", parts.join(" · ")));
    list.appendChild(item);
  });
  box.appendChild(list);

  if (team.notes && team.notes.length > 0) {
    const notes = el("ul", "notes");
    team.notes.forEach((note) => notes.appendChild(el("li", "", note)));
    box.appendChild(notes);
  }

  if (isSelected) {
    const clear = el("button", "link-button", "Clear selection");
    clear.type = "button";
    clear.addEventListener("click", () => selectTeam(state.selectedId, false));
    box.appendChild(clear);
  }
}

// ---------- Startup ----------

function watchChartWidth() {
  const body = document.getElementById("chart-body");
  let timer = null;
  new ResizeObserver(() => {
    // Only redraw when the width really changed (drawing changes height, which would loop).
    if (Math.abs(body.clientWidth - state.chartWidth) < 2 || state.view === "table") return;
    clearTimeout(timer);
    timer = setTimeout(renderChartBody, 60);
  }).observe(body);
}

function render(data) {
  state.data = data;
  state.teams = data.teams.slice().sort((a, b) => b.title_prob - a.title_prob);
  const fromHash = TABS.find((t) => "#" + t.id === location.hash);
  if (fromHash) state.tabId = fromHash.id;

  renderHeader(data);
  renderKpis();
  renderTabs();
  updateTabs();
  renderChartCard();
  renderDetail();
  watchChartWidth();

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selectedId) selectTeam(state.selectedId, false);
  });
  document.getElementById("loading").hidden = true;
  document.getElementById("app").hidden = false;
  // Draw again now that the container is visible and has a real width.
  renderChartBody();
}

async function loadOdds() {
  try {
    const response = await fetch("data/odds.json", { cache: "no-cache" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (!Array.isArray(data.teams) || data.teams.length === 0) {
      throw new Error("the file has no teams");
    }
    render(data);
  } catch (err) {
    showError("Couldn't load the odds (" + err.message + "). Please try again later.");
  }
}

loadOdds();
