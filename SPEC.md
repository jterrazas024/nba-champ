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
| **Basketball-Reference (chosen)** | Team ratings (ORtg/DRtg/NRtg, SRS, SOS), player BPM, minutes and games played (3 seasons), current injuries, monthly game results | Tested Nov 7, 2026: all 7 pages returned HTTP 200 with the needed columns, from a US IP and from GitHub Actions. Blocked (Cloudflare 403) from a Mexican IP, so it can be location-dependent. robots.txt: crawl-delay 3s; disallows `*/gamelog/`, `*/splits/`, `*/on-off/`, `*/lineups/`, `*/shooting/` and `/play-index/` searches. Our limit: one request every 4s, max 20/min, cached daily. Any 403/429 stops the run. |
| cdn.nba.com | Scoreboard, schedule JSON | Not usable: Akamai "Access Denied" (403) from home, hotspot, and a Chicago VPN. Others report 403 from Actions too. |
| stats.nba.com / `nba_api` | Clutch splits, lineup/on-off data | Not usable: requests time out (home, hotspot, VPN); `nba_api` reports the same from cloud IPs. We will not work around the block. |
| balldontlie (backup) | Schedule and scores (free, 5 req/min); injuries and minutes (ALL-STAR, $9.99/mo) | Official API with a key. Not needed for now; see `docs/data-source-research.md`. |

**Per-input source (decided in Phase 3):**

| Input | Source | Notes |
|---|---|---|
| Net rating, SRS | Basketball-Reference season page | Early-season SOS is noisy (e.g. +9.7 margin but SRS 0.2 after 9 games); the preseason-prior blend in 6.3 handles this. |
| Star power (BPM) | Basketball-Reference player advanced page | BPM is published directly. Apply the minimum games/minutes filter (a 2-minute player can show BPM 27). |
| Star and rotation availability | Player advanced pages for the last 3 seasons (G, MP) plus the injuries page | Team games played come from the team table (W+L). |
| Recent form | Basketball-Reference monthly results pages | Scores only, so use point differential per game over the last 20 games (not per 100 possessions). |
| Clutch | Not available from a usable source | Open question (Section 14): close-game proxy from final scores, or drop it. Weights renormalize if absent. |
| Core net rating | Not available (needs per-game player logs; `*/gamelog/` is disallowed by robots.txt) | Stays deferred, as in 6.1. |

**Fallback:** if Basketball-Reference starts blocking GitHub Actions, switch to a keyed API (balldontlie) for the pieces it covers, or run the pipeline from a US-based machine on a schedule and push `odds.json`. Site and deploy don't change. The refresh job must fail loudly and keep the last good file (Section 9).

---

## 6. Model specification

### 6.1 Inputs and starting weights

| Input | Weight | Definition | v0 status |
|---|---|---|---|
| Net rating (SoS-adjusted) | 38% | Points scored minus allowed per 100 possessions: `NRtg + SOS × min(1, games/40)` (schedule strength phased in, since early SOS is noisy), blended with last season's rating (regressed to 70% of the league mean) using `w = games/(games+20)` | Built (v0) |
| Star power | 25% | `0.6 × BPM(Star A) + 0.4 × BPM(Star B)`. Stars = the two highest-BPM players on the roster with at least 15 mpg and at least 40% of the team's games (never fewer than 3 or more than 20) | Built (v0) |
| Recent form | 10% | Average point differential per game over the team's last 20 finished games (the results pages have scores but no possessions); needs at least 5 games | Built (v0) |
| Star availability | 10% | Games played ÷ team games, for Star A and B, over the last 3 seasons weighted 0.5 / 0.3 / 0.2 (most recent first), averaged; multiplied by an injury factor (Out For Season ×0.25, Out ×0.6, Day To Day ×0.9; day-to-day entries older than 14 days are ignored) | Built (v0) |
| Healthy / core net rating | 10% | Team net rating in games where both stars played | **Dropped** (needs per-game player logs, which the source's robots.txt disallows); weight 0 |
| Rotation availability | 5% | Top 9 players by minutes: minutes-weighted share of team games each has played | Built (v0) |
| Clutch | 2% | Win % in clutch games (last 5 min of 4th/OT, score within 5), compared against non-clutch win % | **Dropped** (no usable data source); weight 0 |

**Weights in use** (`pipeline/config.py`): the five built inputs keep their points (38 / 25 / 10 / 10 / 5) renormalized to sum to 1.0, i.e. each divided by 88: net rating 43.2%, star power 28.4%, recent form 11.4%, star availability 11.4%, rotation availability 5.7%. A team missing one input has its remaining weights renormalized, with a note.

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

Rules: 30 teams; `title_prob` sums to 1.0 (±1e-6); `rank` is 1-30 by `title_prob`; missing inputs are `null` and excluded from the contribution total; file under 100 KB. All 7 input keys always appear: dropped inputs (`core_net_rating`, `clutch`) have weight `0.0` and are `null` for every team, and the site hides them. `prob_change_1d` is `null` when there is no previous real file (the first real run, or the previous file was sample data) and otherwise compares with the previous committed file; real daily history comes in Phase 7. Each team has at most 4 `notes`. The schema may grow, but fields above can't be removed or renamed without updating the pipeline, site, and tests together.

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
- **Cache:** raw pulls cached to avoid hammering sources and to stay under rate limits. A run makes about 8 to 10 requests (ratings for this and last season, player pages for three seasons, injuries, and 1 to 3 monthly results pages), one every 4 seconds, so it takes about 30 seconds; Actions starts each run with an empty cache.
- **Nothing to do:** before the season starts, and after the regular season ends, the build exits successfully without changing the file (playoff odds come in Phase 7).
- **Checks before writing:** 30 teams, enough players per team, no NaN, the SPEC contract (`pipeline/validate.py`), and no team's title probability moving more than 15 points in one update. The file is written to a temporary name and swapped in.

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
| Data source blocks us (already happened: stats.nba.com, cdn.nba.com, and Basketball-Reference from a non-US IP) | Phase 3 tested every source; Basketball-Reference works from Actions today. Never work around a block; stop on 403/429; keep the last good `odds.json`; fall back to a keyed API |
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
| Nov 8, 2026 | Phase 3: Basketball-Reference is the data source (works from Actions); do not use stats.nba.com or cdn.nba.com; never evade bot protection |
| Nov 8, 2026 | Added `lxml` as a dependency (reads Basketball-Reference's HTML tables) |
| Nov 8, 2026 | Phase 4: drop clutch and core net rating; renormalize the remaining weights (net rating 38, star power 25, recent form 10, star availability 10, rotation availability 5, each divided by 88) |
| Nov 8, 2026 | Preseason prior: last season's net rating regressed toward the league mean |
| Nov 8, 2026 | Net rating = NRtg + SOS ramped in as `min(1, games/40)` (raw SRS is too noisy early); prior regressed to 70% |
| Nov 8, 2026 | v0 uses minutes/games thresholds for stars; no early-season BPM shrinkage |
| Nov 8, 2026 | Star injury factors: Out For Season x0.25, Out x0.6, Day To Day x0.9 (config values); an injured star still counts in star power, with a note |
| Nov 8, 2026 | `odds.json` keeps all 7 input keys; dropped inputs have weight 0.0 and null values (no schema change) |
| Nov 8, 2026 | After the regular season ends, v0 stops refreshing (no-op) until the Phase 7 playoff simulation |
| Nov 8, 2026 | `prob_change_1d` is null on the first real run, then compares with the previous committed file (real history is Phase 7) |

## 14. Open questions

- Is one successful Actions run enough? Re-check after the first week of scheduled runs; Cloudflare can change its mind.
- Daily refresh time, and whether to refresh more often on game days.
