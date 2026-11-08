// Renders data/odds.json as an odds board (SPEC.md section 8).
// Plain script, no dependencies. All text goes in with textContent (never innerHTML),
// so nothing in the JSON file can ever be interpreted as HTML.

const STALE_AFTER_HOURS = 48;

// Display names for the model inputs, in the order they appear in the drill-down.
const INPUT_LABELS = {
  net_rating: "Net rating",
  star_power: "Star power",
  recent_form: "Recent form",
  star_availability: "Star availability",
  core_net_rating: "Core net rating",
  rotation_availability: "Rotation availability",
  clutch: "Clutch",
};

// Small helper: create an element with optional class and text.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatPercent(prob) {
  return (prob * 100).toFixed(1) + "%";
}

// Change since yesterday, in percentage points, e.g. "▲ 0.4".
function formatChange(delta) {
  const points = Math.abs(delta * 100).toFixed(1);
  if (Number(points) === 0) return { text: "–", cls: "" };
  return delta > 0
    ? { text: "▲ " + points, cls: "up" }
    : { text: "▼ " + points, cls: "down" };
}

function formatNumber(value, digits) {
  return value === null || value === undefined ? "n/a" : Number(value).toFixed(digits);
}

function showError(message) {
  document.getElementById("loading").hidden = true;
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
}

// Header: season, "Sample data" badge, last-updated time, stale notice.
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

// The expandable panel with each input's value, percentile, and contribution.
function buildDetail(team, id) {
  const panel = el("div", "detail");
  panel.id = id;
  panel.hidden = true;

  panel.appendChild(el("p", "detail-meta",
    team.conference + "ern Conference · Record " + team.record +
    " · Composite " + formatNumber(team.composite, 2)));

  const table = el("table", "inputs");
  const head = el("thead");
  const headRow = el("tr");
  ["Input", "Value", "Percentile", "Contribution"].forEach((label) =>
    headRow.appendChild(el("th", "", label)));
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  Object.keys(INPUT_LABELS).forEach((key) => {
    const input = team.inputs && team.inputs[key];
    const row = el("tr");
    row.appendChild(el("th", "", INPUT_LABELS[key]));
    row.appendChild(el("td", "", input ? formatNumber(input.value, 2) : "n/a"));
    row.appendChild(el("td", "", input ? String(input.percentile) : "n/a"));
    row.appendChild(el("td", "", input ? formatNumber(input.contribution, 3) : "n/a"));
    body.appendChild(row);
  });
  table.appendChild(body);
  panel.appendChild(table);

  if (team.notes && team.notes.length > 0) {
    const list = el("ul", "notes");
    team.notes.forEach((note) => list.appendChild(el("li", "", note)));
    panel.appendChild(list);
  }
  return panel;
}

// One row of the board. The whole row is a <button>, so it works with keyboard and screen readers.
function buildRow(team, maxProb) {
  const item = el("li", "row");
  const detailId = "detail-" + team.id;

  const button = el("button", "row-button");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", detailId);

  button.appendChild(el("span", "rank", String(team.rank)));

  const name = el("span", "name", team.name);
  name.appendChild(el("small", "", team.record));
  button.appendChild(name);

  // The bar is scaled to the leader so differences are easy to see.
  // The percentage next to it is the text equivalent of the bar.
  button.appendChild(el("span", "prob", formatPercent(team.title_prob)));
  const track = el("span", "bar-track");
  track.setAttribute("aria-hidden", "true");
  const fill = el("span", "bar-fill");
  fill.style.width = (maxProb > 0 ? (team.title_prob / maxProb) * 100 : 0) + "%";
  track.appendChild(fill);
  button.appendChild(track);

  const change = formatChange(team.prob_change_1d || 0);
  const changeEl = el("span", "change " + change.cls, change.text);
  changeEl.title = "Change in title probability since yesterday, in percentage points";
  button.appendChild(changeEl);

  const detail = buildDetail(team, detailId);
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!open));
    detail.hidden = open;
  });

  item.appendChild(button);
  item.appendChild(detail);
  return item;
}

function renderBoard(data) {
  const teams = data.teams.slice().sort((a, b) => b.title_prob - a.title_prob);
  const maxProb = teams.length > 0 ? teams[0].title_prob : 0;
  const board = document.getElementById("board");
  teams.forEach((team) => board.appendChild(buildRow(team, maxProb)));
  document.getElementById("loading").hidden = true;
  board.hidden = false;
}

async function loadOdds() {
  try {
    const response = await fetch("data/odds.json", { cache: "no-cache" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (!Array.isArray(data.teams) || data.teams.length === 0) {
      throw new Error("the file has no teams");
    }
    renderHeader(data);
    renderBoard(data);
  } catch (err) {
    showError("Couldn't load the odds (" + err.message + "). Please try again later.");
  }
}

loadOdds();
