# NBA Championship Odds App: Plan & Model Input Research (Revision 2)

*Drafted Nov 7, 2026, about 3 weeks into the 2026-27 season (tipped off Oct 20).*

**What changed in this revision:** Plotly and Dash are out. Version 1 is a plain HTML/CSS/JavaScript site hosted on GitHub Pages, with a scheduled GitHub Action refreshing the data. The goal is a live, self-updating site: plan, build, test, deploy, and keep it running.

---

## 1. How the pieces fit

| Piece | This project's version |
|---|---|
| Get data | A Python script pulls team, player, and game data from NBA sources on a schedule |
| Transform / model | The script computes ratings, star impact, and availability, then turns them into title odds |
| Keep the app fast | Precompute everything in the pipeline and ship a small `odds.json` (the page never touches raw data) |
| Visualize | A hand-built HTML/CSS odds board (no charting library in v1) |
| Publish and keep it running | GitHub Pages hosts the site, and a scheduled GitHub Action refreshes the data and redeploys |

---

## 2. Hosting and architecture

**Decision: static site on GitHub Pages, refreshed by a scheduled GitHub Action.**

- **GitHub Pages** serves plain files (HTML, CSS, JS, JSON). It can't run Python, which is fine because NBA stats only change once a night.
- **A GitHub Action on a daily cron** runs the Python pipeline, writes a fresh `data/odds.json`, commits it, and Pages republishes automatically.
- **The page** loads `odds.json` in the browser and renders the odds board. Opening `index.html` locally, or through a tiny local server, is how you preview changes before pushing.
- **Cost and access:** Pages is free for public repositories. Private repositories need a paid GitHub plan, so plan on a public repo (nothing in this project is sensitive).
- **Timing caveat:** scheduled Actions can start a few minutes late, which is irrelevant for a once-a-day refresh.

### Data-source risk to test on day one
- **stats.nba.com (the `nba_api` Python package)** is the richest source (clutch, on/off, lineups), but it's behind bot protection that drops connections from cloud IP ranges, **including GitHub Actions**. It works from a home connection.
- **cdn.nba.com** (scoreboard, box scores, play-by-play) is not blocked and works from anywhere, including Actions.
- **Basketball-Reference** allows scraping at **no more than 20 requests per minute** (exceed it and you're blocked for up to a day). That's plenty for a once-a-day refresh, as long as the script paces itself and caches pages.
- **balldontlie**: the free tier is basic data only, and advanced stats need a paid plan.

**Fallback if Actions can't reach what we need:** run the pipeline on your own computer on a schedule and push the updated `odds.json` to the repo. The site and the deploy don't change at all.

---

## 3. Research: what actually predicts a champion

### 3.1 Net rating (your instinct is right, and it should be the anchor)
- Regular-season net rating is widely cited as the **single strongest predictor** of playoff success. Since 2000, teams ranked top 4 in net rating won their first-round series 88.1% of the time. *(The Ringer)*
- It also works as an **eliminator**: no champion since 2000 has finished worse than **8th** in regular-season net rating *(The Ringer)*, and every champion back to 1996-97 was top 8 *(SI)*.
- Modern champions have outscored opponents by at least **+4.1 per 100 possessions** *(theScore)*.
- **Last season checks out:** the 2026 champion Knicks finished **53-29, 3rd in the East**, beat the Spurs 4-1 in the Finals *(StatMuse)*, and were about **5th in net rating at +6.4** (3rd offense, 6th defense) *(FadeAway World)*.

### 3.2 Two-way balance
- Every champion since 2004 ranked **17th or better in both offensive and defensive rating** *(Crafted NBA)*. Use this as a soft penalty, not a hard rule.

### 3.3 Recent form (an input you didn't mention, but should consider)
- In March 2026 the Knicks were 18-7 over their last 25 games, and their **+12.3 net rating over that stretch led the NBA**, far above their full-season mark *(Knicks X-Factor)*. Late-season net rating catches teams that improved through trades, health, or rotation changes.

### 3.4 Star power (your A/B star thesis is backed by the data)
- One study of playoff series found that **each extra All-NBA vote share raised the odds of winning a series by about 117%**, holding net rating constant *(BestBallStats)*.
- **Each of the last 16 champions** had a player who ranked top 15 in the league on an average of catch-all metrics *(theScore)*.
- **How to measure it:** catch-all impact metrics such as EPM, LEBRON, DARKO DPM, BPM, and RAPM. NBA executives reportedly trust DARKO the most *(HoopsHype)*. EPM uses play-by-play data and DARKO uses box scores, but for most players the two land very close *(r/NBAanalytics)*.
- **Proposed metric:** `StarPower = 0.6 × (Star A impact) + 0.4 × (Star B impact)`, using a metric we can pull automatically (BPM from Basketball-Reference is the most accessible, and EPM or DARKO if we can get them reliably).
- Supporting point: one analysis found **bench depth predicted the regular season but not the playoffs**, consistent with playoffs being star-driven *(Bruin Sports Analytics)*.

### 3.5 Availability (stars + top-9 rotation)
- **Star availability:** % of possible games played over the last 3 seasons (recent seasons weighted more), adjusted for current injury status.
- **Rotation availability:** for the top 9 players by minutes, a minutes-weighted % of games available.
- **"Healthy net rating" upgrade:** one public power-ranking model weights a team's net rating in games where its best players actually played (plus the net rating of its top 2-3 together) alongside overall net rating *(Crafted NBA)*. We can compute this from box scores, and it may beat a simple games-played %.

### 3.6 Clutch record (recommend a low weight or display-only)
- The definition you were reaching for: NBA "clutch time" is the **last 5 minutes of the 4th quarter or OT with the score within 5 points**.
- The evidence that it predicts playoff success is weak:
  - One year's regular-season-to-playoff clutch net rating correlation was **-0.1**, which is essentially noise *(Nylon Calculus)*.
  - **Every NBA champion in the 29 seasons with clutch data had a better record in non-clutch games** than in clutch games. The regular-season to playoff clutch-record link is small and not statistically significant *(NBA.com, Schuhmann)*.
- **Recommendation:** show it in the drill-down, but give it at most ~5% weight. Treat a big gap between clutch and non-clutch records as a "luck, likely to regress" flag.

### 3.7 Conference standing / seed
- Regular-season seed correlated **0.937** with expected playoff results in one study *(Harvard Sports Analysis)*. Modern champions won at least **.634 of games (52-win pace) with a top-5 record** *(theScore)*.
- Seed matters mostly through **home court and bracket path**. That's best handled by simulating the bracket (Section 4) rather than as a separate weighted score.

### 3.8 Other candidates (optional)
| Factor | Verdict |
|---|---|
| Strength of schedule | **Include**, as an adjustment to net rating (SRS-style) |
| Playoff experience of core | Optional, small modifier. It was used in the series model above |
| Point differential / win % | Skip, because it's redundant with net rating |
| Pace, 3PT rate, rebounding | Skip for v1, since they don't add much once net rating is in |
| **Market odds (Polymarket)** | **Not a model input. Show it next to ours as a "Model vs Market" column**, the most Polymarket-like feature we can add (later version) |

### 3.9 Early-season caution (it's November)
After roughly 10-12 games, net rating is mostly noise. **Blend each team's current stats with a preseason prior** (last season's rating, or the preseason market) and let the current season take over as games accumulate. For example: current-season weight = games played / (games played + 20).

---

## 4. Turning scores into odds

**v0 (build first): Weighted score → softmax.** Normalize each input to a z-score, compute a weighted composite, then `odds_i = exp(score_i / T) / Σ exp(score_j / T)`. The temperature *T* controls how top-heavy the odds look (60/20/5 vs. 30/25/20), and we tune it on the backtest. This is simple enough to get a live site up quickly.

**v1 (upgrade once the site is live): Strength rating → Monte Carlo playoff simulation.**
1. Convert the composite into a "playoff strength" number (points per 100 possessions).
2. Project the final standings, and seed each conference using the 6 seeds + play-in format.
3. Simulate the full bracket 10,000+ times, using a logistic model for single-game win probability that includes home court.
4. Title % = share of simulations each team wins.

The simulation builds conference standing, path difficulty, and home court in naturally, and it outputs real probabilities like a market does. Because the model sits behind a fixed `odds.json` format, swapping v0 for v1 doesn't touch the website.

---

## 5. Starting weights (for us to argue about)

| Input | Starting weight | Role |
|---|---|---|
| Net rating, SoS-adjusted (blended with prior) | **38%** | Core |
| Star power (A 60 / B 40) | **25%** | Core |
| Recent form (net rating, last ~20 games) | **10%** | Modifier |
| Star availability (3-season games-played %, plus current injury) | **10%** | Modifier |
| Healthy / core-lineup net rating | **10%** | Modifier |
| Top-9 rotation availability | **5%** | Modifier |
| Clutch (vs. non-clutch) | **2%** | Display + tiny weight |
| Seed / conference path | via simulation (v1) | Structural |
| Two-way balance (O and D both top ~15) | penalty | Filter |
| Net rating rank worse than ~10th late in the season | heavy penalty | Filter |

These are placeholders. The backtest decides the final numbers.

---

## 6. Backtest plan

1. **Champion profile check (last 10 champions, 2017-2026):** where each ranked in net rating, offense, defense, seed, star metrics, and availability. This confirms or adjusts your "top-7 net / top-5 seed" hunch.
2. **Avoid overfitting:** 10 champions is a tiny sample. Fit weights on **~20+ seasons of playoff series** (about 15 series a year gives 300+ outcomes), then validate on title odds.
3. **No hindsight:** score each past season using only the data available **before the playoffs** (and also at the trade deadline) to see how early the model "knew."
4. **Scoring:** Brier score / log loss on the champion, where the champion ranked in our odds, plus a comparison to pre-playoff betting odds.

The backtest runs after the first live version ships, so it doesn't delay launch. Until then, the starting weights above are used.

---

## 7. Project layout

```
nba-title-odds/
├── CLAUDE.md                  # project rules Claude Code reads every session
├── pipeline/                  # Python: fetch → features → model
│   ├── fetch_*.py             # one module per data source, with caching + rate limits
│   ├── features.py            # net rating, star power, availability, clutch
│   ├── model.py               # composite score → odds (softmax, later simulation)
│   └── build_odds.py          # writes data/odds.json
├── tests/                     # small tests for the math (weights sum to 1, odds sum to 100%)
├── data/
│   ├── raw/                   # cached pulls
│   └── odds.json              # the small snapshot the site reads (the contract)
├── index.html                 # the site: markup
├── style.css                  # Apple-style look
├── app.js                     # loads odds.json and renders the board
└── .github/workflows/
    └── refresh.yml            # daily cron: run pipeline → commit odds.json → Pages redeploys
```

**`odds.json` is the contract between the model and the site.** A sample shape: for each team, the title probability, rank, change since yesterday, and each input's value, percentile, and contribution. The site only reads this file, so we can build the page against fake data on day one and swap in real data later.

Keeping it fast at scale: cache raw pulls so we only fetch new games, aggregate in the pipeline, and ship the browser a file under 100 KB.

---

## 8. UI direction: "Apple builds a prediction market"

- **Home:** a clean odds board with team, big probability %, a thin horizontal probability bar, and a ▲/▼ change since yesterday. Bars and sparklines are plain CSS or small inline SVG, with no charting library.
- **Team drill-down:** click a row to expand a card for each input (net rating, star power, availability, clutch) with its percentile and its contribution to the final odds.
- **Style:** SF-style system font stack, lots of white space, light and dark modes via `prefers-color-scheme`, one accent color, subtle motion. No dashboard clutter.
- **Later versions:** a "Model vs Market" column with Polymarket odds, odds-history sparklines, and a charting library if we want richer visuals.

---

## 9. Roadmap

### Phases
| Phase | Deliverable |
|---|---|
| 0. Setup | GitHub account/repo (public), Python installed, first `CLAUDE.md`, first commit |
| 1. Hello, Pages | A one-page `index.html` with fake `odds.json`, live on GitHub Pages |
| 2. Auto-update | A GitHub Action on a daily cron that rewrites a timestamp in `odds.json`, with the page showing "Last updated" |
| 3. Data spike | Confirm which sources work from your laptop vs. GitHub Actions (Section 2) |
| 4. Real model v0 | Team table with every input, weighted composite → softmax → real `odds.json` |
| 5. Polish | Apple-style UI, drill-down cards, light/dark mode |
| 6. Backtest | Champion profiles and fitted weights (Section 6) |
| 7. Upgrades | Monte Carlo simulation, Model vs Market, optional charts |

**Why this order:** Phases 1 and 2 get a live, self-updating site on the internet before any real modeling exists. Everything after that is improving something already running.

---

## 10. Decisions (confirmed Nov 7, 2026)

1. **Inputs:** keep everything in Section 3 as is.
2. **Clutch:** tiny weight (2%), shown in the drill-down.
3. **Star metric:** BPM to start (upgrade to EPM/DARKO later).
4. **Starting weights:** as in Section 5, with net rating at 38% and clutch at 2%.
5. **Repo:** public GitHub repo (free Pages).
6. **Start:** Phase 0. See `SPEC.md` for the build scope and `CLAUDE.md` for working rules.

---

## Sources
- [The Ringer: NBA title prediction & championship history (2026)](https://www.theringer.com/2026/04/15/nba/nba-title-prediction-2026-championship-history)
- [SI: Predicting the NBA Finals winner from historic trends](https://www.si.com/betting/predicting-the-nba-finals-winner-based-on-historic-statistical-trend)
- [theScore: Championship benchmarks](https://thescore.com/news/2168283)
- [Crafted NBA: Playoff power rankings methodology](https://craftednba.com/blog/nba-playoff-power-rankings)
- [BestBallStats: Predicting playoff series (net rating, All-NBA shares, experience)](https://bestballstats.com/2024/02/19/predicting-nba-playoff-series-does-experience-matter/)
- [Harvard Sports Analysis: Which regular-season stats predict playoff success](https://harvardsportsanalysis.org/2024/06/playoff-prophecy-which-regular-season-stats-spell-postseason-glory/)
- [NBA.com (Schuhmann): Clutch vs non-clutch records and the playoffs](https://www.nba.com/news/celtics-lakers-clutch-games-2025-26)
- [Nylon Calculus: Importance of clutch play](https://fansided.com/2017/04/25/nylon-calculus-russell-westbrook-clutch-play)
- [Bruin Sports Analytics: Depth is overrated](https://www.bruinsportsanalytics.com/post/depth-is-overrated)
- [HoopsHype via Yahoo: Impact metrics executives trust](https://sports.yahoo.com/mvp-race-best-players-based-202748969.html)
- [Knicks X-Factor: Knicks net rating & recent form (Mar 2026)](https://knicksxfactor.com/2026/03/14/knicks-2026-reasons-more-dangerous-than-last-year/)
- [FadeAway World: Knicks 2025-26 ratings](https://fadeawayworld.net/nba/new-york-knicks/can-the-knicks-win-the-2026-nba-championship-5-key-stats-reveal-the-truth)
- [StatMuse: Knicks 2025-26 season](https://www.statmuse.com/nba/ask/knicksc)
- [nba-api-ts: stats.nba.com cloud IP / TLS blocking](https://www.pkgstats.com/pkg:nba-api-ts)
- [Sports Reference: Bot traffic & rate limits](https://www.sports-reference.com/bot-traffic.html)
- [FreeAPIHub: balldontlie free-tier limits](https://freeapihub.com/apis/balldontlie-api)
- [Covers: 2026-27 championship odds incl. Polymarket](https://www.covers.com/nba/nba-championship-odds)
