// Hand-built SVG charts (no libraries). See SPEC.md section 8.
// Exposes one global, `Charts`, used by app.js.
//
// Rules these charts follow:
//  - One series = one color. Emphasis (dimming the rest) highlights a team; color never encodes rank.
//  - Bars are thin, have a rounded tip and a square base, and grow from one baseline (zero).
//  - Gridlines are solid hairlines. Values are labelled at the bar tip, not on every gridline.
//  - Every mark has a hover/focus tooltip AND a keyboard path; the table view is the full-data twin.
//  - Names and numbers go in with textContent only (never innerHTML).

const Charts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Create an SVG element with attributes and optional text.
  function svgEl(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach((k) => node.setAttribute(k, attrs[k]));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // "Nice" axis ticks covering [min, max], e.g. 0, 5, 10, 15, 20, 25.
  // Returns an array; the first and last tick become the axis domain.
  function niceTicks(min, max, target) {
    const span = max - min || 1;
    const rough = span / target;
    const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / magnitude;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * magnitude;
    const start = Math.floor(min / step + 1e-9) * step;
    const end = Math.ceil(max / step - 1e-9) * step;
    const ticks = [];
    for (let v = start; v <= end + step / 1000; v += step) {
      ticks.push(Number((Math.round(v / step) * step).toPrecision(12)));
    }
    return ticks;
  }

  // ---- Tooltip (one shared element, positioned next to the pointer or the focused mark) ----
  const tooltip = {
    node: null,
    get() {
      if (!this.node) this.node = document.getElementById("tooltip");
      return this.node;
    },
    // content = { value, name, detail }: the value leads, the name follows.
    show(clientX, clientY, content) {
      const tip = this.get();
      tip.textContent = "";
      const value = document.createElement("div");
      value.className = "tip-value";
      value.textContent = content.value;
      const name = document.createElement("div");
      name.className = "tip-name";
      name.textContent = content.name;
      tip.appendChild(value);
      tip.appendChild(name);
      if (content.detail) {
        const detail = document.createElement("div");
        detail.className = "tip-detail";
        detail.textContent = content.detail;
        tip.appendChild(detail);
      }
      tip.hidden = false;
      const pad = 14;
      let left = clientX + pad;
      let top = clientY + pad;
      if (left + tip.offsetWidth > window.innerWidth - 8) left = clientX - tip.offsetWidth - pad;
      if (top + tip.offsetHeight > window.innerHeight - 8) top = clientY - tip.offsetHeight - pad;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = Math.max(8, top) + "px";
    },
    hide() {
      this.get().hidden = true;
    },
  };

  // Path for a bar from x0 (baseline) to x1 (tip): rounded at the tip, square at the baseline.
  function barPath(x0, x1, y, h, radius) {
    const length = Math.abs(x1 - x0);
    if (length < 0.5) return "";
    const r = Math.min(radius, length, h / 2);
    const dir = x1 >= x0 ? 1 : -1;
    return (
      "M" + x0 + "," + y +
      "H" + (x1 - dir * r) +
      "Q" + x1 + "," + y + " " + x1 + "," + (y + r) +
      "V" + (y + h - r) +
      "Q" + x1 + "," + (y + h) + " " + (x1 - dir * r) + "," + (y + h) +
      "H" + x0 + "Z"
    );
  }

  // Horizontal bar chart, one row per team.
  // opts: { rows: [{id, name, value}], format(v), tickFormat(v), selectedId, onSelect(id),
  //         tooltip(row) -> {value, name, detail}, ariaLabel }
  function drawBarChart(container, opts) {
    container.textContent = "";
    const width = Math.max(container.clientWidth, 280);
    const compact = width < 520; // narrow screens show the 3-letter id instead of the full name
    const rowH = 26;
    const barH = 18;
    const axisH = 30;
    const labelW = compact ? 44 : 172;

    const values = opts.rows.map((r) => r.value);
    const lo = Math.min(0, ...values);
    const hi = Math.max(0, ...values);
    const ticks = niceTicks(lo, hi, 5);
    const d0 = ticks[0];
    const d1 = ticks[ticks.length - 1];
    const plotL = labelW + (lo < 0 ? 52 : 6); // room for value labels on negative bars
    const plotR = width - 60; // room for value labels at the tip of long bars
    const x = (v) => plotL + ((v - d0) / (d1 - d0)) * (plotR - plotL);
    const height = axisH + opts.rows.length * rowH + 8;

    const svg = svgEl("svg", {
      width, height, viewBox: "0 0 " + width + " " + height,
      role: "group", "aria-label": opts.ariaLabel || "Bar chart",
    });

    // Gridlines and tick labels (hairlines; the zero line is a touch stronger).
    ticks.forEach((t) => {
      const gx = x(t);
      svg.appendChild(svgEl("line", {
        class: t === 0 ? "axis-line" : "grid-line",
        x1: gx, x2: gx, y1: axisH - 6, y2: height - 4,
      }));
      svg.appendChild(svgEl("text", {
        class: "axis-text", x: gx, y: axisH - 14, "text-anchor": "middle",
      }, opts.tickFormat(t)));
    });

    const zeroX = x(0);
    opts.rows.forEach((row, i) => {
      const top = axisH + i * rowH;
      const y = top + (rowH - barH) / 2;
      const isSelected = opts.selectedId === row.id;
      const dimmed = opts.selectedId && !isSelected;

      const group = svgEl("g", {
        class: "bar-group" + (isSelected ? " selected" : "") + (dimmed ? " dim" : ""),
        tabindex: "0", role: "button", "aria-pressed": String(isSelected),
        "aria-label": row.name + ": " + opts.format(row.value),
      });
      // Transparent row-wide hit area: far easier to hit than the bar itself.
      group.appendChild(svgEl("rect", { class: "bar-hit", x: 0, y: top, width, height: rowH, rx: 6 }));
      group.appendChild(svgEl("text", {
        class: "bar-name", x: labelW - 10, y: top + rowH / 2,
        "text-anchor": "end", "dominant-baseline": "central",
      }, compact ? row.id : row.name));

      const tipX = x(row.value);
      group.appendChild(svgEl("path", {
        class: "bar-mark" + (row.value < 0 ? " neg" : ""),
        d: barPath(zeroX, tipX, y, barH, 4),
      }));
      const positive = row.value >= 0;
      group.appendChild(svgEl("text", {
        class: "bar-value", x: tipX + (positive ? 8 : -8), y: top + rowH / 2,
        "text-anchor": positive ? "start" : "end", "dominant-baseline": "central",
      }, opts.format(row.value)));

      const showTip = (clientX, clientY) => tooltip.show(clientX, clientY, opts.tooltip(row));
      group.addEventListener("pointermove", (e) => showTip(e.clientX, e.clientY));
      group.addEventListener("pointerleave", () => tooltip.hide());
      group.addEventListener("focus", () => {
        const box = group.getBoundingClientRect();
        showTip(box.left + Math.min(box.width, 260), box.top + box.height / 2);
      });
      group.addEventListener("blur", () => tooltip.hide());
      group.addEventListener("click", () => opts.onSelect(row.id));
      group.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          opts.onSelect(row.id);
        }
      });
      svg.appendChild(group);
    });
    container.appendChild(svg);
  }

  // Bubble scatter: x and y are two inputs, dot area shows title probability.
  // opts: { points: [{id, name, x, y, size}], xLabel, yLabel, xTick(v), yTick(v), selectedId,
  //         onSelect(id), tooltip(point), ariaLabel, labelCount }
  function drawScatter(container, opts) {
    container.textContent = "";
    const width = Math.max(container.clientWidth, 280);
    const height = Math.round(Math.min(Math.max(width * 0.72, 340), 580));
    const m = { l: 56, r: 24, t: 16, b: 52 };

    const xs = opts.points.map((p) => p.x);
    const ys = opts.points.map((p) => p.y);
    const xt = niceTicks(Math.min(...xs), Math.max(...xs), 6);
    const yt = niceTicks(Math.min(...ys), Math.max(...ys), 6);
    const sx = (v) => m.l + ((v - xt[0]) / (xt[xt.length - 1] - xt[0])) * (width - m.l - m.r);
    const sy = (v) => height - m.b - ((v - yt[0]) / (yt[yt.length - 1] - yt[0])) * (height - m.t - m.b);
    const maxSize = Math.max(...opts.points.map((p) => p.size));
    // Radius grows with the square root of probability so dot AREA tracks the value. Minimum 4px (8px dot).
    const radius = (p) => 4 + Math.sqrt(p.size / maxSize) * 14;

    const svg = svgEl("svg", {
      width, height, viewBox: "0 0 " + width + " " + height,
      role: "group", "aria-label": opts.ariaLabel || "Scatter chart",
    });

    xt.forEach((t) => {
      svg.appendChild(svgEl("line", { class: t === 0 ? "axis-line" : "grid-line", x1: sx(t), x2: sx(t), y1: m.t, y2: height - m.b }));
      svg.appendChild(svgEl("text", { class: "axis-text", x: sx(t), y: height - m.b + 18, "text-anchor": "middle" }, opts.xTick(t)));
    });
    yt.forEach((t) => {
      svg.appendChild(svgEl("line", { class: t === 0 ? "axis-line" : "grid-line", x1: m.l, x2: width - m.r, y1: sy(t), y2: sy(t) }));
      svg.appendChild(svgEl("text", { class: "axis-text", x: m.l - 8, y: sy(t), "text-anchor": "end", "dominant-baseline": "central" }, opts.yTick(t)));
    });
    svg.appendChild(svgEl("text", { class: "axis-title", x: m.l + (width - m.l - m.r) / 2, y: height - 8, "text-anchor": "middle" }, opts.xLabel));
    svg.appendChild(svgEl("text", {
      class: "axis-title", x: 14, y: m.t + (height - m.t - m.b) / 2, "text-anchor": "middle",
      transform: "rotate(-90 14 " + (m.t + (height - m.t - m.b) / 2) + ")",
    }, opts.yLabel));

    // Draw big dots first so small dots are never hidden underneath.
    const ordered = opts.points.slice().sort((a, b) => b.size - a.size);
    const dots = [];

    // Decide where (and whether) each name label goes. Labels go to the selected team and the
    // biggest few dots. A label tries right, left, above, then below the dot, and is skipped
    // if every spot would hit another dot, another label, or the plot edge (the tooltip still has it).
    const selectedPoint = opts.points.find((p) => p.id === opts.selectedId);
    const labelWanted = ordered.slice(0, opts.labelCount || 5).filter((p) => p !== selectedPoint);
    if (selectedPoint) labelWanted.unshift(selectedPoint); // the selected team is placed first, so it always gets a spot
    const placedBoxes = [];
    const labelAt = {};
    const hitsDot = (box) => ordered.some((q) => {
      const nx = Math.max(box.x0, Math.min(sx(q.x), box.x1));
      const ny = Math.max(box.y0, Math.min(sy(q.y), box.y1));
      return Math.hypot(nx - sx(q.x), ny - sy(q.y)) < radius(q) + 2;
    });
    const hitsLabel = (box) => placedBoxes.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0);
    labelWanted.forEach((p) => {
      const cx = sx(p.x);
      const cy = sy(p.y);
      const r = radius(p) + (p.id === opts.selectedId ? 5 : 0); // keep clear of the selection ring
      const w = p.name.length * 6.4; // rough text width at 12px
      const sideBox = (x0, x1) => ({ x0, x1, y0: cy - 8, y1: cy + 8 });
      const aboveBox = (x0, x1) => ({ x0, x1, y0: cy - r - 17, y1: cy - r - 2 });
      const belowBox = (x0, x1) => ({ x0, x1, y0: cy + r + 3, y1: cy + r + 18 });
      const candidates = [
        { anchor: "start", x: cx + r + 5, y: cy, box: sideBox(cx + r + 4, cx + r + 6 + w) },
        { anchor: "end", x: cx - r - 5, y: cy, box: sideBox(cx - r - 6 - w, cx - r - 4) },
        { anchor: "middle", x: cx, y: cy - r - 9, box: aboveBox(cx - w / 2, cx + w / 2) },
        { anchor: "middle", x: cx, y: cy + r + 11, box: belowBox(cx - w / 2, cx + w / 2) },
        { anchor: "end", x: cx + r, y: cy - r - 9, box: aboveBox(cx + r - w, cx + r) },
        { anchor: "end", x: cx + r, y: cy + r + 11, box: belowBox(cx + r - w, cx + r) },
      ];
      const spot = candidates.find((c) =>
        c.box.x0 >= m.l && c.box.x1 <= width - 4 && c.box.y0 >= m.t - 4 && c.box.y1 <= height - m.b &&
        !hitsDot(c.box) && !hitsLabel(c.box));
      if (spot) {
        placedBoxes.push(spot.box);
        labelAt[p.id] = spot;
      }
    });
    ordered.forEach((p) => {
      const cx = sx(p.x);
      const cy = sy(p.y);
      const isSelected = opts.selectedId === p.id;
      const dimmed = opts.selectedId && !isSelected;
      const group = svgEl("g", {
        class: "dot-group" + (isSelected ? " selected" : "") + (dimmed ? " dim" : ""),
        tabindex: "0", role: "button", "aria-pressed": String(isSelected),
        "aria-label": p.name + ": " + opts.xLabel + " " + opts.xTick(p.x) + ", " + opts.yLabel + " " + opts.yTick(p.y),
      });
      if (isSelected) group.appendChild(svgEl("circle", { class: "dot-halo", cx, cy, r: radius(p) + 5 }));
      group.appendChild(svgEl("circle", { class: "dot", cx, cy, r: radius(p) }));
      const spot = labelAt[p.id];
      if (spot) {
        group.appendChild(svgEl("text", {
          class: "dot-label", x: spot.x, y: spot.y, "text-anchor": spot.anchor, "dominant-baseline": "central",
        }, p.name));
      }
      group.addEventListener("focus", () => {
        const box = group.getBoundingClientRect();
        tooltip.show(box.right, box.top, opts.tooltip(p));
      });
      group.addEventListener("blur", () => tooltip.hide());
      group.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          opts.onSelect(p.id);
        }
      });
      dots.push({ p, cx, cy, group });
      svg.appendChild(group);
    });

    // Nearest-point hover: the pointer only has to be CLOSEST to a dot, not dead-center on it.
    const REACH = 36; // pixels
    function nearest(e) {
      const box = svg.getBoundingClientRect();
      const px = e.clientX - box.left;
      const py = e.clientY - box.top;
      let best = null;
      let bestDist = REACH;
      dots.forEach((d) => {
        const dist = Math.hypot(d.cx - px, d.cy - py);
        if (dist < bestDist) { best = d; bestDist = dist; }
      });
      return best;
    }
    let hovered = null;
    svg.addEventListener("pointermove", (e) => {
      const hit = nearest(e);
      if (hovered && hovered !== hit) hovered.group.classList.remove("hover");
      hovered = hit;
      if (hit) {
        hit.group.classList.add("hover");
        tooltip.show(e.clientX, e.clientY, opts.tooltip(hit.p));
        svg.style.cursor = "pointer";
      } else {
        tooltip.hide();
        svg.style.cursor = "";
      }
    });
    svg.addEventListener("pointerleave", () => {
      if (hovered) hovered.group.classList.remove("hover");
      hovered = null;
      tooltip.hide();
    });
    svg.addEventListener("click", (e) => {
      const hit = nearest(e);
      if (hit) opts.onSelect(hit.p.id);
    });
    container.appendChild(svg);
  }

  return { drawBarChart, drawScatter, hideTooltip: () => tooltip.hide() };
})();
