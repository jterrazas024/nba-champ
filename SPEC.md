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
| Site | `index.html`, `style.css`, `app.js`, `fonts/` | No build step, no dependencies |
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

### 6.4 v1 upgrade: playoff simulation (shadow mode)

`pipeline/sim.py` converts the composite to a strength in points of margin (`MARGIN_PER_Z x composite`, `MARGIN_PER_Z = 3.3`), adds per-run rating noise (sd `3.0 x sqrt(20 / (games + 20))` points), plays the remaining regular-season schedule with a logistic single-game model (`p_home = 1 / (1 + exp(-(r_home - r_away + 2.5) / 6.5))`), ranks each conference (random tie-breaks), runs the 2020-21 play-in for seeds 7-10, then best-of-7 series (home court 2-2-1-1-1) through the Finals. Title % is the share of 20,000 runs won, rounded with `model.round_probs`. It can start from a bracket with series in progress (eliminated teams get exactly 0). A daily seed from `pipeline/seed.py` (SHA-256 of date, season, model version) makes a re-run reproducible.

Status: **runs in the daily build in shadow mode** (since Nov 8, 2026): its numbers are published in additive fields (section 7), and the softmax is still the headline; the site does not display them. Strength uses the composite **before** the penalties (`config.SIM_USES_PENALTIES = False`; the simulation models seeding itself), and the schedule is every unplayed regular-season game on the monthly results pages from the current month through April (`pipeline/sim_inputs.py`). The published schedule can be a few games short for some teams (games added once the NBA Cup knockout rounds are set), so up to 4 missing games per team are filled with random opponents (seeded by the daily seed); a team more than 4 short, or more than 1 over, means a page is wrong and the simulation is skipped. Constants live in `pipeline/config.py`; `MARGIN_PER_Z` was fitted once on 2026-11-08 to the softmax's title probabilities, with `python pipeline/calibrate_sim.py` (offline script). Evidence and the comparison with the softmax on 26 seasons: `docs/simulation-report.md` (the simulation is indistinguishable from the softmax out of sample). `pipeline/schedule.py` reads whole monthly results pages (played and unplayed games, regular season / play-in / playoffs).

### 6.5 Backtest (Phase 6)

- Champion profile check for the last 10 champions (net rating rank, offense/defense rank, seed, star BPM, availability).
- Fit/validate weights on 20+ seasons of playoff series using only pre-playoff data.
- Metrics: Brier score and log loss on the champion, where the champion ranked in our odds, compared against pre-playoff betting odds.
- **Done (Nov 8, 2026):** code in `pipeline/backtest/`, results and recommendation in `docs/backtest-report.md` (26 seasons, 2000-01 to 2025-26; no betting odds were collected). Weights and temperature were left unchanged.

### 6.6 Playoff mode (after game 82)

**When.** Once every team has played 82 games (the ratings page keeps showing 82 during the playoffs), the daily build switches to playoff mode and stays in it until the new season's ratings page shows games (August on, `config.season_end_year`). Before the season starts the build still does nothing.

**What it reads.** `pipeline/playoffs.py` takes the April, May and June monthly results pages (cache names shared with the recent-form and schedule fetches; a May or June page that does not exist yet counts as no games, a block stops the run) and the final standings from the ratings page. Layout facts, checked on the cached 2022-23, 2023-24 and 2024-25 pages: April holds the end of the regular season, the play-in (notes "Play-In Game") and the first playoff games with no marker, so `pipeline/schedule.py` separates them by date; **May and June pages hold only playoff games but carry no marker, so the schedule parser would call them "regular"; the reader parses them with `playoffs_from` set before every game.** The ratings page's W-L excludes playoff games.

**What it builds.** A `sim.PlayoffState`: standings (wins, then margin of victory) give the six direct qualifiers and the four play-in teams per conference; played play-in games are fixed results (game 1: 7 hosts 8; game 2: 9 hosts 10; game 3: loser of game 1 hosts winner of game 2; the winner is the 8 seed) and the rest are simulated; once a conference's play-in is over its eight seeds are fixed. Playoff games are grouped by pair of teams into series (round 1 is 1v8, 4v5, 3v6, 2v7; later rounds follow the winners) and each started series enters with its score; a series at 4 wins puts the loser out. Real seeding tie-breaks are not modeled: when first-round games exist the seeds are corrected to the closest order that fits the observed pairings and home teams. Any game that does not fit a bracket raises `ParseError` and the run fails (the previous file stays): in playoff mode a wrong bracket is worse than a stale one.

**Headline decision (reasoning).** The softmax is a regular-season-strength model; it cannot know who is eliminated and keeps giving lottery teams a chance. Options were (a) keep the softmax and only show the sim beside it, (b) zero the eliminated teams and renormalize the softmax, (c) make the simulation the headline. Chosen: **(c), the least risky one**: in playoff mode `title_prob` is the simulation's conditioned title probability (same value as `sim_prob`). It already sums to 1.0, gives eliminated teams exactly 0 and the champion exactly 1.0, needs no extra rule, and uses the same strength (`MARGIN_PER_Z x composite`) the softmax uses, so the only change is the structure (bracket, series state). Option (b) would invent a second ad hoc model; option (a) would publish probabilities for teams that are out. The softmax number stays in each team's `softmax_prob` for comparison. `rank` is by `title_prob`, ties (every eliminated team at 0) broken by composite, as before. Weights, temperature and the star metric are untouched.

**Freeze.** When the Finals are decided the simulation returns exactly 1.0 for the champion and 0 for everyone else, `phase` is `"complete"`, and the build keeps succeeding and validating every day until the next season's pages appear.

**Daily guard and `prob_change_1d`.** The 15-point limit exists to catch broken data in a slow-moving regular season; in playoff mode large moves are real (a game 7, a team knocked out). So in playoff mode (`headline_source == "simulation"`) the 15-point limit is **not applied**; in its place a "no revivals" check refuses any file in which a team the previous playoff-mode file marked `eliminated` has title odds, plus the validator rules in section 7 (sums to 1, eliminated is 0, champion is 1.0, one champion, `title_prob == sim_prob`). On the transition day (previous file's `headline_source` is `softmax`, new one `simulation`) `prob_change_1d` is `null` for every team, because the change would mix a model switch with real eliminations; the next day it resumes. `history` is one continuous series (it simply steps on the switch day) and `model_version` is unchanged so it is not reset.

**Other notes.** `notes` start with where a team went out ("Missed the playoffs", "Lost in the play-in", "Lost in the second round", "Won the championship"). Recent form still uses the last 20 finished games, which in playoff mode include postseason games. Cost: about 10 requests in May/June, under the cap of 20. Offline replay of past postseasons at chosen dates: `python pipeline/replay_playoffs.py YEAR DATE... --pages DIR...` (uses SRS for strength, not the daily composite).

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
  "history_dates": ["2026-11-07", "2026-11-08"],
  "sim": {"runs": 20000, "seed_date": "2026-11-08", "mode": "regular_season", "margin_per_z": 3.3},
  "teams": [
    {
      "id": "SAS",
      "name": "San Antonio Spurs",
      "conference": "West",
      "record": "11-4",
      "title_prob": 0.221,
      "rank": 1,
      "prob_change_1d": 0.004,
      "history": [0.217, 0.221],
      "sim_prob": 0.205,
      "sim": {"finals": 0.31, "conf_finals": 0.52, "make_playoffs": 0.97, "seed_avg": 2.4},
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

Rules: 30 teams; `title_prob` sums to 1.0 (±1e-6); `rank` is 1-30 by `title_prob`; missing inputs are `null` and excluded from the contribution total; file under 100 KB. All 7 input keys always appear: dropped inputs (`core_net_rating`, `clutch`) have weight `0.0` and are `null` for every team, and the site hides them. `history_dates` (top level) and `history` (per team) hold the last up to 30 daily `title_prob` values, oldest first, with matching UTC dates (`YYYY-MM-DD`, strictly increasing, same length as every team's `history`). The previous `odds.json` is the only source: its history is carried forward and today's value is added; a second run on the same UTC date overwrites that date's entry; missed days stay as gaps (no backfill). History starts fresh (just today) when the previous file is missing, unreadable, sample data, or from a different `model_version`. A previous real file without history is bootstrapped once: its own date (from `generated_at`) and `title_prob` values seed the history. `prob_change_1d` is today's value minus the value on the most recent earlier date in `history`, and is `null` if that date is more than 3 days back or there is none. Validation of these fields runs only when they are present, so older files still pass. Each team has at most 4 `notes`.

**Playoff-mode fields (additive, section 6.6).** Top level: `phase` (`"regular_season"`, `"playin"`, `"playoffs"` or `"complete"`) and `headline_source` (`"softmax"` before game 82, `"simulation"` after; in that case `title_prob` equals `sim_prob`), and `sim.mode` carries the phase. Per team in playoff mode only: `status` (`"alive"`, `"eliminated"` or `"champion"`) and `softmax_prob` (the regular-season model's number, for comparison). Validation: eliminated means `title_prob` 0, champion means exactly 1.0, at most one champion and exactly one when `phase` is `"complete"`.

**Simulation fields (shadow mode, additive).** `sim_prob` (per team) is the title probability from the playoff simulation (section 6.4), rounded with `model.round_probs` so the 30 values sum to exactly 1.0. Per-team `sim` holds `finals`, `conf_finals` and `make_playoffs` (shares of simulated seasons, in [0, 1], 4 decimals) and `seed_avg` (average playoff seed in the runs where the team made the playoffs, 2 decimals, between 1 and 15; `null` if it never did). Top-level `sim` holds `runs` (a positive integer), `seed_date` (the UTC date that seeds the random numbers, so a same-day rerun gives the same numbers), `mode` (`"regular_season"`) and `margin_per_z`. **The headline fields (`title_prob`, `rank`, `composite`, `inputs`, `prob_change_1d`, `history`) are unchanged and still come from the softmax; the site does not show the sim fields yet.** The sim fields are optional: they are absent on days the simulation stage failed (section 9), and validation of them runs only when present. The file should stay under about 50 KB (the hard limit is 100 KB). The schema may grow, but fields above can't be removed or renamed without updating the pipeline, site, and tests together.

---

## 8. Site specification

- **Layout:** a wide dashboard (up to about 1320px). A sticky top bar shows the title, season, "Sample data" badge, and "Last updated" time. Below it, headline tiles: the favorite (the one large hero number), top-3 combined probability, and the number of teams above 5%. Then a chart card (about two thirds of the width) next to a team panel (about one third, sticky). On screens under 1000px everything stacks in one column.
- **Chart tabs** (all hand-built SVG, no charting library): **Odds** (title probability, all 30 teams, sorted), **Net rating** (with a switch to core net rating), **Star power**, **Recent form**, **Availability** (switch between star and rotation), **Clutch**, and **Contenders** (net rating against star power, dot size = title probability). The tab is shared in the URL hash, e.g. `#net-rating`.
- **Chart rules:** one color for all bars (no rank coloring); the selected team is emphasized by dimming the rest; negative values are gray bars left of the zero line; bars are thin with a rounded tip; values are labelled at the bar tip; gridlines are solid hairlines. Every chart has a hover/keyboard tooltip (value, team, record, percentile) and a **Table** view with the same numbers. **Sparklines** (hand-built inline SVG, one thin accent-colored line with an end dot, about 96x24 px, `role="img"` with a text label such as "Title odds over the last 30 days: from 17.9% to 18.4%") draw each team's `history` in the team panel (under the big probability, captioned "Last N days") and as a "Trend" column in the Odds table view. They are hidden when a team has fewer than 3 history points, and the site still works with an `odds.json` that has no history fields. Larger daily history charts are not built yet.
- **Team panel:** shows the selected team (the favorite when none is selected): title probability, rank, change since the previous update (`prob_change_1d`, a dash when null), the sparkline, and each model input with its value, percentile meter, weight, and contribution, plus notes (e.g., clutch regression flag). Click a bar, dot, or table row to select; click again, "Clear selection", or Escape to clear.
- **Style:** Apple-like restraint (system font stack, generous white space, rounded cards, subtle transitions) combined with a prediction-market dashboard feel (Polymarket/Kalshi: wide layout, big numbers, tabs, green accent). Light and dark mode via `prefers-color-scheme`.
- **Responsive:** works on phone and desktop; on narrow screens bars show 3-letter team codes.
- **Failure states:** if `odds.json` fails to load, show a clear message; if `is_sample` is true, show a visible "Sample data" badge; if `generated_at` is more than 48 hours old, show a "Data may be stale" notice.
- **Accessibility:** semantic HTML, keyboard-operable tabs, bars, dots, and rows, sufficient contrast, and a table view equivalent for every chart (no value is available only on hover).

---

## 9. Automation

- **Workflow:** `.github/workflows/refresh.yml`, triggers: daily cron (pick an off-peak time after games finish, 10:17 UTC, an off-peak minute because GitHub delays or drops runs at the top of the hour) and `workflow_dispatch` for manual runs.
- **Steps:** check out the repo → set up Python → install requirements → run tests → run `build_odds.py` → if `odds.json` changed, commit and push to `main`.
- **Failure behavior:** the job fails visibly (email from GitHub) and leaves the last good `odds.json` in place. Never commit a file that fails validation.
- **Permissions:** the workflow needs `contents: write` to push the update.
- **Cache:** raw pulls cached to avoid hammering sources and to stay under rate limits. A run makes about 13 to 16 requests in November and fewer later in the season (ratings for this and last season, player pages for three seasons, injuries, 1 to 3 monthly results pages for recent form, plus the monthly pages from the current month through April for the remaining schedule; the current month's page is shared and fetched once), one every 4 seconds, so it takes about 1 minute; `MAX_REQUESTS` stays 20; Actions starts each run with an empty cache.
- **Failure isolation for the simulation (shadow mode):** the simulation is optional. If anything in that stage fails (a schedule page cannot be fetched or read, the site blocks us, the simulator raises, the sanity checks fail), the build still publishes the normal softmax odds without the `sim` fields and prints one `WARNING` line to stderr (visible in the Action log). A block (403/429/bot check) during the schedule fetches stops all further requests but keeps the odds already computed. Failures in the required model pages (ratings, players, injuries, recent form) still fail the whole run and keep the previous file.
- **Nothing to do:** before the season starts the build exits successfully without changing the file. After game 82 it runs in playoff mode (section 6.6); there the simulation is required, and a failure to read the bracket fails the run (previous file kept).
- **Checks before writing:** 30 teams, enough players per team, no NaN, the SPEC contract (`pipeline/validate.py`), and (regular season only) no team's title probability moving more than 15 points in one update; in playoff mode the 15-point limit is replaced by the no-revival check (section 6.6). The file is written to a temporary name and swapped in.

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
| Nov 8, 2026 | Phase 6 backtest (26 seasons, nested leave-one-season-out): regularized refit gained only 0.016-0.038 log loss with standard errors of 0.03-0.05, so weights and temperature stay as they are (see `docs/backtest-report.md`) |
| Nov 8, 2026 | Added `numpy` as a dependency (backtest fitting only; not used by the daily build) |
| Nov 8, 2026 | `prob_change_1d` is null on the first real run, then compares with the previous committed file (real history is Phase 7) |
| Nov 8, 2026 | Phase 7: odds history lives inside `odds.json` (`history_dates` plus a per-team `history`, last 30 days, carried forward from the previous file; no separate history file); `prob_change_1d` now comes from it and is null if the earlier entry is more than 3 days old |
| Nov 8, 2026 | Phase 7 (offline part): playoff simulation built in `pipeline/sim.py` with `MARGIN_PER_Z = 3.3` fitted to today's softmax; 26-season backtest shows it indistinguishable from the softmax (log loss 1.84 vs 1.91, s.e. of the difference 0.07). Not live; headline stays the softmax until the owner decides (`docs/simulation-report.md`) |
| Nov 8, 2026 | Playoff mode (section 6.6): after game 82 the headline `title_prob` comes from the state-conditioned simulation (the softmax cannot know eliminations); the softmax stays in `softmax_prob`; the 15-point guard is replaced by a no-revival check in playoff mode; `prob_change_1d` is null on the switch day; new additive fields `phase`, `headline_source`, `status`, `softmax_prob` |
| Nov 8, 2026 | Shadow mode: the playoff simulation runs in the daily build and is published in new fields only (`sim_prob`, `sim`); the softmax stays the headline. The simulation's strength uses the composite before penalties (`SIM_USES_PENALTIES = False`), since it models seeding itself. The headline switches only after at least two weeks of comparison and the owner's decision |

## 14. Open questions

- Is one successful Actions run enough? Re-check after the first week of scheduled runs; Cloudflare can change its mind.
- Daily refresh time, and whether to refresh more often on game days.
