# SPEC: NBA Championship Odds

Scope of work and technical specification. Companion files: `CLAUDE.md` (working rules) and `docs/research-plan.md` (research and sources).

**Status:** Draft v1, Nov 7, 2026. Season in progress (2026-27 tipped off Oct 20; regular season ends Apr 11, 2027; playoffs start Apr 17, 2027).

---

## 1. Goals

1. **Ship a live site** at a GitHub Pages URL that shows championship odds for all 30 teams and updates automatically every day.
2. **Make the odds defensible:** driven by inputs that research shows predict playoff success, with a backtest to check them.

## 2. Non-goals (version 1)

- No Plotly, Dash, or any charting/UI framework.
- No server, database, user accounts, or live in-game updates.
- No betting advice or real-money features. The site is for information only, and says so.
- No Monte Carlo bracket simulation until the site is live (planned upgrade, Phase 7).
- No Polymarket comparison until a later version.

## 3. Users

Anyone with the public link who wants a quick view of title odds and why.

---

## 4. Architecture

```
[ Data sources ] → [ Python pipeline ] → data/odds.json → [ Static site on GitHub Pages ]
                         ↑
        GitHub Action on daily cron (or local run as fallback)
```

| Layer | Choice | Notes |
|---|---|---|
| Pipeline | Python 3.11+, pandas, requests | Runs locally and in GitHub Actions |
| Contract | `data/odds.json` | Only thing the site reads |
| Site | `index.html`, `style.css`, `app.js` | No build step, no dependencies |
| Hosting | GitHub Pages, public repo, `main` branch root | Free for public repos |
| Automation | `.github/workflows/refresh.yml` | Daily cron plus manual "Run workflow" button |
| Tests | pytest | Math and data-shape checks |

### Repo layout

```
nba-title-odds/
├── CLAUDE.md
├── SPEC.md
├── README.md                  # short public description (created in Phase 0)
├── requirements.txt
├── .gitignore
├── docs/research-plan.md
├── pipeline/
│   ├── config.py              # weights, constants, paths
│   ├── fetch_team_stats.py
│   ├── fetch_player_stats.py
│   ├── fetch_games.py
│   ├── features.py
│   ├── model.py
│   └── build_odds.py          # entry point, writes data/odds.json
├── tests/
├── data/
│   ├── raw/                   # git-ignored cache
│   └── odds.json
├── index.html
├── style.css
├── app.js
└── .github/workflows/refresh.yml
```

---

## 5. Data sources

| Source | Used for | Access notes |
|---|---|---|
| Basketball-Reference | Team ratings (ORtg/DRtg/NRtg, SRS), player BPM and minutes, game logs, standings | Max 20 requests/min; cache everything; scraping must stay polite |
| cdn.nba.com | Scoreboard, box scores, play-by-play | Not blocked from cloud IPs |
| stats.nba.com via `nba_api` | Clutch splits, lineup/on-off data | Blocked from cloud IPs; test before relying on it |
| Phase 3 task | Decide the final source per input after testing from laptop and from GitHub Actions | Record the result in this file |

**Fallback:** if a needed source is unreachable from GitHub Actions, run the pipeline locally on a schedule and push `odds.json`. Site and deploy don't change.

---

## 6. Model specification

### 6.1 Inputs and starting weights

| Input | Weight | Definition | v0 status |
|---|---|---|---|
| Net rating (SoS-adjusted) | 38% | Points scored minus allowed per 100 possessions, adjusted for schedule (use SRS or opponent-adjusted NRtg), blended with a preseason prior | Build |
| Star power | 25% | `0.6 × BPM(Star A) + 0.4 × BPM(Star B)`. Stars = the two highest-BPM players on the roster with at least 15 mpg and a minimum games threshold | Build |
| Recent form | 10% | Net rating over the team's last 20 games | Build |
| Star availability | 10% | Games played ÷ team games, for Star A and B, over the last 3 seasons weighted 0.5 / 0.3 / 0.2 (most recent first), averaged; reduced if currently injured | Build |
| Healthy / core net rating | 10% | Team net rating in games where both stars played | Later (needs game logs); renormalize weights until built |
| Rotation availability | 5% | Top 9 players by minutes: minutes-weighted share of team games each has played | Build |
| Clutch | 2% | Win % in clutch games (last 5 min of 4th/OT, score within 5), compared against non-clutch win % | Build; also shown as a regression-risk flag |

### 6.2 Filters / penalties

- **Two-way balance:** if a team's offensive or defensive rating rank is worse than 17th, apply a penalty (starting value: subtract 0.5 from the composite z-score).
- **Late-season net rating gate:** from March 1 on, a team ranked worse than 10th in net rating gets a heavy penalty (starting value: subtract 1.5).
- Penalty sizes are tunable in `config.py` and set by the backtest.

### 6.3 From inputs to odds (v0)

1. Compute each input for all 30 teams.
2. Convert each input to a z-score across the 30 teams.
3. Early-season blend: for net rating, `current_weight = games_played / (games_played + 20)`, and the remainder comes from the preseason prior (last season's net rating, regressed toward the league mean).
4. `composite_i = Σ (weight_k × z_ik)` minus any penalties. If an input is missing, drop it and renormalize the remaining weights to sum to 1.
5. `title_prob_i = exp(composite_i / T) / Σ_j exp(composite_j / T)`.
6. `T` (temperature) starts at 0.6 and is tuned on the backtest so the spread looks like a real market (favorite roughly 15-30% in-season; sharper late in the year).

### 6.4 v1 upgrade: playoff simulation (later)

Convert composite to a strength rating, project final standings, seed the bracket using the 6 seeds + play-in format, simulate 10,000+ playoff brackets with a logistic single-game model that includes home court, and report title % as share of simulations won. Output goes into the same `odds.json`.

### 6.5 Backtest (Phase 6)

- Champion profile check for the last 10 champions (net rating rank, offense/defense rank, seed, star BPM, availability).
- Fit/validate weights on 20+ seasons of playoff series using only pre-playoff data.
- Metrics: Brier score and log loss on the champion, where the champion ranked in our odds, compared against pre-playoff betting odds.

---

## 7. `odds.json` schema

```json
{
  "generated_at": "2026-11-08T12:00:00Z",
  "season": "2026-27",
  "is_sample": false,
  "games_played_league_avg": 15.2,
  "model_version": "v0",
  "temperature": 0.6,
  "weights": {"net_rating": 0.38, "star_power": 0.25, "recent_form": 0.10,
              "star_availability": 0.10, "core_net_rating": 0.10,
              "rotation_availability": 0.05, "clutch": 0.02},
  "teams": [
    {
      "id": "SAS",
      "name": "San Antonio Spurs",
      "conference": "West",
      "record": "11-4",
      "title_prob": 0.221,
      "rank": 1,
      "prob_change_1d": 0.004,
      "composite": 1.42,
      "inputs": {
        "net_rating":        {"value": 7.1,  "z": 1.6, "percentile": 94, "contribution": 0.61},
        "star_power":        {"value": 5.4,  "z": 1.9, "percentile": 97, "contribution": 0.48}
      },
      "notes": ["Clutch record 5-1 vs non-clutch 6-3: likely to regress"]
    }
  ]
}
```

Rules: 30 teams; `title_prob` sums to 1.0 (±1e-6); `rank` is 1-30 by `title_prob`; missing inputs are `null` and excluded from the contribution total; file under 100 KB. The schema may grow, but fields above can't be removed or renamed without updating the pipeline, site, and tests together.

---

## 8. Site specification

- **Layout:** a wide dashboard (up to about 1320px). A sticky top bar shows the title, season, "Sample data" badge, and "Last updated" time. Below it, headline tiles: the favorite (the one large hero number), top-3 combined probability, and the number of teams above 5%. Then a chart card (about two thirds of the width) next to a team panel (about one third, sticky). On screens under 1000px everything stacks in one column.
- **Chart tabs** (all hand-built SVG, no charting library): **Odds** (title probability, all 30 teams, sorted), **Net rating** (with a switch to core net rating), **Star power**, **Recent form**, **Availability** (switch between star and rotation), **Clutch**, and **Contenders** (net rating against star power, dot size = title probability). The tab is shared in the URL hash, e.g. `#net-rating`.
- **Chart rules:** one color for all bars (no rank coloring); the selected team is emphasized by dimming the rest; negative values are gray bars left of the zero line; bars are thin with a rounded tip; values are labelled at the bar tip; gridlines are solid hairlines. Every chart has a hover/keyboard tooltip (value, team, record, percentile) and a **Table** view with the same numbers. Daily history charts wait until the pipeline stores snapshots (Phase 7).
- **Team panel:** shows the selected team (the favorite when none is selected): title probability, rank, change since yesterday, and each model input with its value, percentile meter, weight, and contribution, plus notes (e.g., clutch regression flag). Click a bar, dot, or table row to select; click again, "Clear selection", or Escape to clear.
- **Style:** Apple-like restraint (system font stack, generous white space, rounded cards, subtle transitions) combined with a prediction-market dashboard feel (Polymarket/Kalshi: wide layout, big numbers, tabs, green accent). Light and dark mode via `prefers-color-scheme`.
- **Responsive:** works on phone and desktop; on narrow screens bars show 3-letter team codes.
- **Failure states:** if `odds.json` fails to load, show a clear message; if `is_sample` is true, show a visible "Sample data" badge; if `generated_at` is more than 48 hours old, show a "Data may be stale" notice.
- **Accessibility:** semantic HTML, keyboard-operable tabs, bars, dots, and rows, sufficient contrast, and a table view equivalent for every chart (no value is available only on hover).

---

## 9. Automation

- **Workflow:** `.github/workflows/refresh.yml`, triggers: daily cron (pick an off-peak time after games finish, e.g., 10:00 UTC) and `workflow_dispatch` for manual runs.
- **Steps:** check out the repo → set up Python → install requirements → run tests → run `build_odds.py` → if `odds.json` changed, commit and push to `main`.
- **Failure behavior:** the job fails visibly (email from GitHub) and leaves the last good `odds.json` in place. Never commit a file that fails validation.
- **Permissions:** the workflow needs `contents: write` to push the update.
- **Cache:** raw pulls cached to avoid hammering sources and to stay under rate limits.

---

## 10. Testing

- `odds.json` validation: 30 teams, probabilities sum to 1, ranks correct, no NaN, size limit.
- Config: weights sum to 1.0; missing-input renormalization works.
- Math: z-scores, softmax, early-season blend, availability calculations tested on small hand-checked examples.
- Fetch modules: parsing tests on saved sample pages (no live network in tests).
- Site: manual check in browser each change (light/dark, phone width, load failure).

---

## 11. Phases and acceptance criteria

| Phase | Deliverable | Done when |
|---|---|---|
| 0. Setup | GitHub account and public repo, Python installed, project folder with `CLAUDE.md`, `SPEC.md`, `README.md`, `.gitignore`, first commit pushed | Repo is on GitHub with the docs and first commit pushed |
| 1. Hello, Pages | `index.html`, `style.css`, `app.js` rendering a sample `odds.json` (`is_sample: true`), live on GitHub Pages | The public URL shows the odds board with a "Sample data" badge |
| 2. Auto-update | `refresh.yml` runs daily and by button; updates `generated_at`; page shows "Last updated" | A manual run changes the timestamp on the live site without touching your laptop |
| 3. Data spike | Test every candidate source from laptop and from Actions; record results in Section 5 | Each model input has a confirmed data source that works in the chosen run location |
| 4. Model v0 | Fetch → features → composite → softmax → real `odds.json`, with tests | Live site shows real odds, summing to 100%, refreshed daily |
| 5. Polish | Apple-style UI, drill-down panels, light/dark mode, responsive layout | Looks good on phone and desktop; failure states work |
| 6. Backtest | Champion profiles, fitted weights and temperature | Report in `docs/` and `config.py` updated |
| 7. Upgrades | Monte Carlo simulation, Model vs Market (Polymarket), odds history sparklines, optional charting library | Each shipped as its own branch and merge |

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| NBA stats blocked from GitHub Actions | Test in Phase 3; fall back to local scheduled run or alternative sources |
| Scraping blocks / terms limits | Respect 20 requests/min, cache, fetch once per day |
| Early-season noise | Preseason prior blend (Section 6.3) |
| Source page layout changes break parsing | Parsing tests on saved samples; Action fails loudly; last good file stays live |
| Overfitting the weights on a small sample | Fit on 20+ seasons of series data, validate out of sample |
| Publishing bad data | Validation step blocks commits of invalid `odds.json` |
| Public repo exposes secrets | No secrets in code; use Actions secrets and `.env` (ignored) |

---

## 13. Decisions log

| Date | Decision |
|---|---|
| Nov 7, 2026 | Drop Plotly/Dash for v1; plain HTML/CSS/JS on GitHub Pages |
| Nov 7, 2026 | Inputs per research plan Section 3 |
| Nov 7, 2026 | Clutch weight 2% (display-focused) |
| Nov 7, 2026 | Star metric: BPM to start |
| Nov 7, 2026 | Starting weights per Section 6.1 (net rating 38%) |
| Nov 7, 2026 | Public GitHub repo |
| Nov 7, 2026 | Start with Phase 0 |

## 14. Open questions

- Which exact source gives SoS-adjusted net rating and clutch splits that work from Actions? (Phase 3)
- Preseason prior: last season's net rating regressed to the mean, or the preseason betting market? (Decide in Phase 4)
- Daily refresh time, and whether to refresh more often on game days.
