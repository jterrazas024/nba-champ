# Simulation report (Phase 7, offline)

The playoff simulation (`pipeline/sim.py`) is built and tested but **not yet used by the daily build**: `odds.json` still shows the softmax. This report is the evidence for deciding when, or whether, to make the simulation the headline. Code: `pipeline/calibrate_sim.py` (offline only, reads cached pages, no network). Rerun: `python pipeline/calibrate_sim.py`.

## 1. What the simulation does

Strength is `MARGIN_PER_Z x composite` (points of margin against an average team; the composite includes the live penalties). Each run draws a rating error per team (sd 3.0 points at 0 games, shrinking as `sqrt(20 / (games + 20))`), plays every remaining regular-season game with `p(home) = 1 / (1 + exp(-(r_home - r_away + 2.5) / 6.5))`, ranks each conference (random tie-breaks, a simplification), plays the 2020-21 play-in for seeds 7-10, then best-of-7 series with 2-2-1-1-1 home court through the Finals. Title probability is the share of 20,000 runs won, rounded so the 30 numbers sum to exactly 1.0. It can also start mid-series (`PlayoffState`). Runtime: a quarter of a second to a few seconds for 20,000 runs.

## 2. Today's data (2026-11-08): fitting `MARGIN_PER_Z`

The real composite is rebuilt from the cached pages exactly as `build_odds.build` does (it reproduces the live `odds.json`). Only October and November results pages are cached, so the 1,098 games still to play are the 171 unplayed games on the November page plus **made-up filler** (random pairings, balanced game counts) for December to April. Opponents are therefore an average draw, which slightly understates real schedule differences.

`MARGIN_PER_Z` was chosen to minimize the sum of squared gaps to the softmax's title probabilities: **3.3** (the fit curve is flat between 3.2 and 3.5). It is stored in `config.py` and is not refit in the daily run.

| | Simulation | Softmax |
|---|---|---|
| Favorite (SAS) | 16-17% | 17.9% |
| Rank correlation with the other method | 0.975 | |
| Mean / max absolute gap in title % | 0.7 / about 3.5-4.0 points | |

(Rerunning with different filler schedules moves these by about 0.1; sampling error at 20,000 runs is about 0.25 points for a favorite.)

**Why they differ.** The simulation knows things the softmax does not:
- *Conferences and seeding.* The East is weaker, so a strong East team has an easier path. The simulation gives the East 36-37% of the title and the softmax 28%; DET goes from 7.8% to 11.8%, BOS 4.9% to 7.4%, CLE and NYK about +1.3 each. Strong West teams (SAS, OKC) give some back.
- *The play-in and bracket luck.* Teams near 7th to 10th can miss the playoffs (e.g. DEN 62%, POR 65% to make it); the softmax has no such step.
- *Early-season rating uncertainty.* With about 9 games played, ratings carry about 2.5 points of noise per team. Without that noise the favorite would be 25% rather than 16-17% at the same margin, so the fitted margin is partly a joint choice with the uncertainty setting.

The comparison is a sanity check and a calibration of scale, not a verdict: matching the softmax does not make either right.

## 3. Past seasons: does the simulation forecast champions better?

26 seasons (2000-01 to 2025-26), strength built from the Phase 6 inputs at the end of the regular season, standings from the ratings pages (wins, conference), top-8 bracket before 2021 and the play-in format from 2021, 20,000 runs per season. Constants for the simulation (margin; for the main variant also the rating-uncertainty setting 0, 3 or 6 points) are chosen **leave-one-season-out**; the softmax uses T=0.6 as live, or T refit the same way. Simulated shares of exactly 0 are floored at 1 in 40,000.

| Model | Log loss | Brier | Champion p (mean) | Top-1 | Top-3 | Top-5 | Median rank |
|---|---|---|---|---|---|---|---|
| Softmax, live (T=0.6) | 1.907 | 0.799 | 0.188 | 0.35 | 0.65 | 0.77 | 2 |
| Softmax, live composite, T refit | 1.892 | 0.797 | 0.206 | 0.35 | 0.65 | 0.77 | 2 |
| Softmax, net rating only, T refit | 1.914 | 0.795 | 0.228 | 0.31 | 0.62 | 0.92 | 3 |
| Simulation, live composite | 1.841 | 0.797 | 0.211 | 0.38 | 0.69 | 0.85 | 2 |
| Simulation, no penalties | 1.815 | 0.779 | 0.232 | 0.42 | 0.69 | 0.85 | 2 |
| Simulation, net rating only | 1.772 | 0.791 | 0.235 | 0.31 | 0.69 | 0.92 | 3 |

Paired per-season differences in log loss (negative = the simulation is better; s.e. is the standard error over 26 seasons):
- Simulation vs the live softmax: **-0.066, s.e. 0.070**, better in 14 of 26 seasons.
- Like for like with the same T-refit treatment: live composite **-0.051, s.e. 0.071** (11 of 26); net rating only **-0.142, s.e. 0.059** (16 of 26).
- The best margin over all 26 seasons was 3.5 (leave-one-season-out picks ranged 3.0 to 4.0), close to the 3.3 fitted on today's data.

**Reading.** The simulation is nominally better on every champion measure except Brier, which is a tie, but the gap for the live composite is about one standard error: **indistinguishable from the softmax on this evidence**. The one result near conventional significance (net rating only, about 2.4 s.e.) is one of several comparisons I looked at, so I would not lean on it. It is mildly encouraging that the structure (seeding, bracket, home court) does not hurt and may help when the inputs are the same. Dropping the penalties helped the simulation slightly (-0.092 vs the live softmax, again within noise), a hint that the penalties may double-count what the bracket already does; I did not change them.

Limits: 26 champions; no betting odds to compare against; record seeds and random tie-breaks instead of the real tie-break rules and division-winner seeding; the 2020 bubble is treated as a top-8 bracket seeded by record; end-of-season only (the simulation's early-season behavior, where it should differ most, is not scored here); backtest inputs have no injury report; strength comes from the same inputs the softmax uses, so this tests the *conversion* to probabilities, not the inputs.

## 4. Suggestion and what the owner needs to decide

- The simulation is a defensible replacement for the softmax: same ordering, more structure (conference path, play-in, home court), and it can condition on series in progress, which the softmax cannot. The data do not show it is more accurate.
- Decisions: (1) whether to make it the headline once the mid-playoff conditioning is wired in (the main reason to switch is playoff-time behavior, not forecast accuracy); (2) whether to keep the penalties inside the simulation's strength; (3) the headline will move noticeably for East teams (seeding effect, section 2), which the site should explain.

## 5. Findings about the pages (for the playoff phase)

- Up to the 2020 season, playoff games sit after a separator row reading "Playoffs". Since the play-in began there is no separator: play-in games carry the note "Play-In Game" and the playoff games after them are unmarked. `pipeline/schedule.py` assigns a stage by date for these pages.
- The Phase 6 history loader (`regular_season_html`) only cuts at the separator, so in 2022-2026 the April pages' playoff games (about 40 per season) are included in the "last 20 games" recent-form input of the backtest. This affects Phase 6 numbers slightly and was **not** changed here.
