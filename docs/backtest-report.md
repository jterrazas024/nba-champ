# Backtest report (Phase 6)

Does the model's championship ranking hold up against history, and should its weights or temperature change? Short answer: the model is reasonable, the data cannot show a clear improvement, so **`pipeline/config.py` is unchanged**.

Code: `pipeline/backtest/` (kept out of the daily build). Run with `python pipeline/backtest/fetch_history.py` (one-off download, cached under `data/raw/backtest/`) and `python pipeline/backtest/run_backtest.py`.

## 1. Data used

Basketball-Reference only, through the project's polite client (one request every 4 seconds, cached, stop on any block). 140 requests in total, no errors or blocks. Per season Y from 2000-01 to 2025-26 (26 seasons; the 2000-01 to 2003-04 leagues had 29 teams):

- team ratings page for Y and Y-1 (net rating, SOS, record, conference standings)
- player advanced pages for Y, Y-1, Y-2 (BPM, games, minutes)
- the monthly results pages (usually March and April; July/August for the 2019-20 bubble, May for 2020-21) until every team had 20 regular-season games
- the playoffs page (every series result and the champion)

Franchises are mapped to today's ids (Sonics to OKC, Nets to BKN, Bobcats to CHA, original Hornets to NOP, Vancouver to MEM). Only regular-season games before the "Playoffs" row are used; play-in games are dropped. All 26 seasons parsed with no missing inputs and no team short of 20 games.

## 2. Method

For each season the inputs are rebuilt as of the last regular-season game and fed through the **live** `features.py` and `model.py` (a test confirms the fast numpy version used for fitting equals the live model). `parse.py` got one change: optional name/code maps so historical team names work; the default behavior is identical.

Known limits, as they affect the numbers:
- No historical injury reports, so no injury factor. Star availability is games played only (this season, last, and the one before), so it is a regular-season durability measure, not "healthy for the playoffs".
- Recent form is the last 20 regular-season games (point difference per game). The prior is the previous season's ratings page. At 82 games the prior has a 20% weight.
- Availability divides previous seasons by 82 games, which understates players in the shortened 1998-99, 2011-12, 2019-20 and 2020-21 seasons (the same for every team, so z-scores are barely affected).
- Seeds are "record seeds" (rank by wins within the conference), ignoring the old division-winner rule and tie-breakers.
- No pre-playoff betting odds were collected, so there is no comparison with the market.
- Only 26 champions. A season's champion log loss has a standard deviation of roughly 0.7, so differences below about 0.1 in the average are hard to separate from luck.

**Metrics.** Per season: the champion's rank in our odds, its probability, log loss (minus the natural log of that probability), and Brier (sum of squared errors over all teams, champion target 1). Overall: averages plus top-1/3/5 hit rates. Lower log loss and Brier are better; a uniform guess scores 3.40 and 0.97.

**Fitting.** Candidates are weight vectors on a 5-point grid over the 5 live inputs (10,626 of them, plus the current weights) times 13 temperatures. A candidate's score is the sum of the champion's log-probability over the training seasons minus a Gaussian penalty for distance from the current weights and T=0.6 (strength `kappa`; larger stays closer to the current model). `kappa` is chosen by leave-one-season-out inside each training set (nested), so the judged seasons never influence any choice. A second fit uses the 390 playoff series (P(A beats B) = logistic of the score difference over T), which has many more observations.

## 3. Champion profiles

Rank among all teams at the end of the regular season (1 = best). `nrtg_rank` is raw NRtg, `adj_nrtg_rank` is the model's SOS-adjusted value, `drtg_rank` is 1 for the stingiest defense, `seed` is the record seed within the conference, `star_rank` and `avail_rank` rank the model's star power and star availability.

| year | champion | teams | nrtg_rank | adj_nrtg_rank | ortg_rank | drtg_rank | seed | conf | wins | win_rank | star_bpm | star_rank | star_avail | avail_rank |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2001 | LAL | 29 | 8 | 5 | 2 | 21 | 2 | West | 56 | 2 | 6.4 | 2 | 0.82 | 22 |
| 2002 | LAL | 29 | 2 | 2 | 2 | 7 | 2 | West | 58 | 2 | 6.6 | 1 | 0.88 | 16 |
| 2003 | SAS | 29 | 3 | 3 | 7 | 3 | 2 | West | 60 | 1 | 5.3 | 7 | 0.93 | 8 |
| 2004 | DET | 29 | 2 | 5 | 18 | 2 | 2 | East | 54 | 6 | 3.5 | 9 | 0.95 | 8 |
| 2005 | SAS | 30 | 1 | 1 | 8 | 1 | 2 | West | 59 | 2 | 7.2 | 2 | 0.88 | 14 |
| 2006 | MIA | 30 | 6 | 5 | 7 | 9 | 2 | East | 52 | 5 | 5.7 | 4 | 0.84 | 24 |
| 2007 | SAS | 30 | 1 | 1 | 5 | 2 | 3 | West | 58 | 3 | 7.4 | 1 | 0.91 | 10 |
| 2008 | BOS | 30 | 1 | 1 | 9 | 1 | 1 | East | 66 | 1 | 6.7 | 3 | 0.87 | 20 |
| 2009 | LAL | 30 | 3 | 3 | 3 | 6 | 1 | West | 65 | 2 | 5.3 | 5 | 0.93 | 4 |
| 2010 | LAL | 30 | 6 | 3 | 11 | 4 | 1 | West | 57 | 3 | 4.6 | 5 | 0.9 | 11 |
| 2011 | DAL | 30 | 8 | 8 | 8 | 7 | 3 | West | 57 | 4 | 4.2 | 9 | 0.96 | 4 |
| 2012 | MIA | 30 | 4 | 4 | 8 | 4 | 2 | East | 46 | 4 | 9.6 | 1 | 0.89 | 15 |
| 2013 | MIA | 30 | 2 | 2 | 2 | 9 | 1 | East | 66 | 1 | 8.7 | 1 | 0.83 | 12 |
| 2014 | SAS | 30 | 1 | 1 | 6 | 3 | 1 | West | 62 | 1 | 4.7 | 6 | 0.74 | 24 |
| 2015 | GSW | 30 | 1 | 1 | 2 | 1 | 1 | West | 67 | 1 | 7.6 | 2 | 0.96 | 1 |
| 2016 | CLE | 30 | 4 | 4 | 3 | 10 | 1 | East | 57 | 3 | 6.7 | 4 | 0.92 | 6 |
| 2017 | GSW | 30 | 1 | 1 | 1 | 2 | 1 | West | 67 | 1 | 8.1 | 1 | 0.84 | 19 |
| 2018 | GSW | 30 | 3 | 3 | 3 | 11 | 2 | West | 58 | 3 | 7.5 | 2 | 0.8 | 21 |
| 2019 | TOR | 30 | 3 | 3 | 5 | 5 | 2 | East | 58 | 2 | 5.5 | 8 | 0.75 | 26 |
| 2020 | LAL | 30 | 5 | 5 | 11 | 3 | 1 | West | 52 | 3 | 8.1 | 2 | 0.85 | 12 |
| 2021 | MIL | 30 | 4 | 3 | 5 | 10 | 3 | East | 46 | 7 | 6.8 | 4 | 0.81 | 11 |
| 2022 | GSW | 30 | 4 | 4 | 17 | 1 | 3 | West | 53 | 3 | 4.9 | 10 | 0.59 | 26 |
| 2023 | DEN | 30 | 6 | 6 | 5 | 14 | 1 | West | 53 | 4 | 8.6 | 1 | 0.84 | 5 |
| 2024 | BOS | 30 | 1 | 1 | 1 | 3 | 1 | East | 64 | 1 | 5.0 | 8 | 0.81 | 11 |
| 2025 | OKC | 30 | 1 | 1 | 3 | 1 | 1 | West | 68 | 1 | 8.6 | 1 | 0.78 | 12 |
| 2026 | NYK | 30 | 5 | 5 | 3 | 7 | 3 | East | 53 | 6 | 3.4 | 15 | 0.69 | 20 | 

- last 10 (10 seasons): NetRtg rank<=7: 10, adj<=7: 10, seed<=5: 10, both(adj): 10, NetRtg top3: 5, seed 1: 5, ortg<=10 & drtg<=10: 6, star rank<=5: 6, max NetRtg rank 6, max seed 3
- all (26 seasons): NetRtg rank<=7: 24, adj<=7: 25, seed<=5: 26, both(adj): 25, NetRtg top3: 15, seed 1: 12, ortg<=10 & drtg<=10: 19, star rank<=5: 18, max NetRtg rank 8, max seed 3

**Reading it.** The hunch holds for the last 10 champions: every one was top 6 in net rating and a top-3 seed (so "top 7 in net rating and top 5 seed" is, if anything, loose). Over all 26 seasons the champion was top 8 in net rating every time (top 7 in 24 of 26, 25 on the SOS-adjusted measure) and never worse than a 3 seed. About half were top 3 in net rating (15 of 26) and the number 1 seed won 12 of 26. Champions are not required to be great on both ends (offense and defense both top 10: 19 of 26; Golden State 2022 had the 17th-ranked offense). Star BPM is usually high (top 5 in 18 of 26) but not always (2026 New York ranked 15th). Star availability is weak or even backwards: champions' availability ranked 10th or worse in most seasons and 24th or 26th in several, which fits stars on contenders sitting out regular-season games.

## 4. Per-season results of the current model (T=0.6)

| season | champion | rank | prob_% | log_loss | brier | our_favorite | fav_prob_% |
|---|---|---|---|---|---|---|---|
| 2001 | LAL | 6 | 6.4 | 2.75 | 1.034 | UTA | 26.1 |
| 2002 | LAL | 2 | 22.3 | 1.5 | 0.721 | SAS | 27.7 |
| 2003 | SAS | 2 | 18.4 | 1.7 | 0.776 | DAL | 26.4 |
| 2004 | DET | 5 | 4.5 | 3.11 | 1.163 | SAS | 36.1 |
| 2005 | SAS | 1 | 27.3 | 1.3 | 0.61 | SAS | 27.3 |
| 2006 | MIA | 7 | 7.1 | 2.64 | 0.998 | SAS | 22.0 |
| 2007 | SAS | 1 | 41.2 | 0.89 | 0.434 | SAS | 41.2 |
| 2008 | BOS | 1 | 17.2 | 1.76 | 0.773 | BOS | 17.2 |
| 2009 | LAL | 2 | 14.6 | 1.93 | 0.887 | CLE | 34.3 |
| 2010 | LAL | 5 | 8.0 | 2.53 | 0.977 | CLE | 25.1 |
| 2011 | DAL | 7 | 8.0 | 2.53 | 0.99 | MIA | 28.9 |
| 2012 | MIA | 1 | 31.5 | 1.15 | 0.566 | MIA | 31.5 |
| 2013 | MIA | 2 | 27.0 | 1.31 | 0.676 | OKC | 33.0 |
| 2014 | SAS | 3 | 12.7 | 2.06 | 0.904 | OKC | 26.6 |
| 2015 | GSW | 1 | 38.6 | 0.95 | 0.438 | GSW | 38.6 |
| 2016 | CLE | 4 | 8.4 | 2.47 | 1.072 | GSW | 42.4 |
| 2017 | GSW | 1 | 44.0 | 0.82 | 0.363 | GSW | 44.0 |
| 2018 | GSW | 2 | 13.6 | 1.99 | 0.899 | HOU | 34.0 |
| 2019 | TOR | 8 | 7.6 | 2.57 | 0.97 | MIL | 18.7 |
| 2020 | LAL | 2 | 16.7 | 1.79 | 0.836 | MIL | 31.7 |
| 2021 | MIL | 1 | 19.3 | 1.64 | 0.754 | MIL | 19.3 |
| 2022 | GSW | 10 | 5.4 | 2.92 | 0.997 | BOS | 13.5 |
| 2023 | DEN | 1 | 18.4 | 1.69 | 0.758 | DEN | 18.4 |
| 2024 | BOS | 2 | 18.6 | 1.68 | 0.77 | DEN | 22.5 |
| 2025 | OKC | 1 | 46.7 | 0.76 | 0.318 | OKC | 46.7 |
| 2026 | NYK | 8 | 4.4 | 3.13 | 1.083 | OKC | 33.0 | 

The model ranked the champion first in 9 of 26 seasons and in the top 3 in 17. The big misses (2004, 2006, 2011, 2019, 2022, 2026) are champions that were not the best team on paper.

## 5. Out-of-sample comparison

All rows are evaluated on seasons the model did not see. Baselines refit their temperature the same way (leave-one-season-out). "Current weights, T refit" keeps the live weights and only re-chooses T.

**Leave-one-season-out (26 seasons)**

| model | log_loss | brier | mean_p_champ_% | top1 | top3 | top5 | median_rank |
|---|---|---|---|---|---|---|---|
| Uniform | 3.396 | 0.966 | 3.4 | 0.0 | 0.0 | 0.0 | 30.0 |
| Win% only | 1.997 | 0.834 | 20.3 | 0.27 | 0.69 | 0.88 | 3.0 |
| Net rating only | 1.914 | 0.795 | 22.8 | 0.31 | 0.62 | 0.92 | 3.0 |
| Current (as live) | 1.907 | 0.799 | 18.8 | 0.35 | 0.65 | 0.77 | 2.0 |
| Current weights, T refit | 1.892 | 0.797 | 20.6 | 0.35 | 0.65 | 0.77 | 2.0 |
| Fitted (regularized) | 1.891 | 0.79 | 21.7 | 0.31 | 0.65 | 0.81 | 2.0 |
| Fitted on series (T for champion refit) | 1.87 | 0.79 | 22.3 | 0.31 | 0.65 | 0.85 | 2.0 | 

**Train on earlier seasons only, test 2010-11 to 2025-26 (16 seasons)**

| model | log_loss | brier | mean_p_champ_% | top1 | top3 | top5 | median_rank |
|---|---|---|---|---|---|---|---|
| Uniform | 3.401 | 0.967 | 3.3 | 0.0 | 0.0 | 0.0 | 30.0 |
| Win% only | 1.917 | 0.797 | 23.5 | 0.38 | 0.62 | 0.88 | 3.0 |
| Net rating only | 1.934 | 0.789 | 24.4 | 0.31 | 0.56 | 0.88 | 3.0 |
| Current (as live) | 1.843 | 0.775 | 20.1 | 0.38 | 0.69 | 0.75 | 2.0 |
| Current weights, T refit | 1.824 | 0.77 | 21.9 | 0.38 | 0.69 | 0.75 | 2.0 |
| Fitted (regularized) | 1.804 | 0.768 | 21.3 | 0.31 | 0.69 | 0.75 | 2.0 |
| Fitted on series (T for champion refit) | 1.846 | 0.793 | 21.7 | 0.38 | 0.69 | 0.75 | 2.0 | 

Fitted minus current log loss per season: leave-one-out mean -0.016 (standard error 0.046, better in 15 of 26 seasons); earlier-to-later mean -0.038 (standard error 0.030, better in only 6 of 16). The regularized `kappa` was 0.3 to 3 in most folds.

**Series level (390 series, leave-one-season-out log loss per series; coin flip is 0.693)**

| model | log_loss_per_series |
|---|---|
| Coin flip | 0.6931 |
| Win% only (T refit) | 0.527 |
| Net rating only (T refit) | 0.5427 |
| Current weights (T refit) | 0.5509 |
| Series-fitted regularized | 0.5432 | 
(series: 390)

Findings:
1. All three real signals beat uniform (3.40) by a wide margin and beat nothing-but-record by a small one.
2. The current composite is barely better than **net rating alone** on the champion (1.907 vs 1.914 log loss; it loses on top-5 hit rate, 77% vs 92%, and on series). Star power, form and the others add very little measurable value on this sample.
3. A win-percentage-only model is the best at series level (0.527 vs 0.551 for the composite). That is probably home-court advantage, which the composite does not include and which the Phase 7 simulation should add.
4. Refitting weights gives at most a 0.016 to 0.04 log-loss gain, well inside the noise.

## 6. Fitted weights and how stable they are

Fits on all 26 seasons (champion objective), in input order net rating / star power / form / star availability / rotation availability:

| kappa | T | weights | in-sample log loss |
|---|---|---|---|
| current model | 0.60 | 43 / 28 / 11 / 11 / 6 | 1.907 |
| 3 | 0.50 | 45 / 30 / 10 / 10 / 5 | 1.879 |
| 1 (nested choice) | 0.50 | 50 / 30 / 15 / 0 / 5 | 1.820 |
| 0.3 | 0.50 | 55 / 30 / 10 / 0 / 5 | 1.813 |
| 0.1 | 0.50 | 60 / 30 / 10 / 0 / 0 | 1.807 |

Bootstrap over seasons (300 resamples, kappa 1; the 10 to 90 percent range of the fitted value):

| input | current | bootstrap mean | 10-90% |
|---|---|---|---|
| net rating | 0.43 | 0.49 | 0.45 - 0.50 |
| star power | 0.28 | 0.31 | 0.25 - 0.35 |
| recent form | 0.11 | 0.13 | 0.10 - 0.15 |
| star availability | 0.11 | 0.02 | 0.00 - 0.05 |
| rotation availability | 0.06 | 0.06 | 0.00 - 0.10 |
| temperature | 0.60 | 0.50 | 0.40 - 0.60 |

What is stable: net rating and star power are the two big inputs (together about 75-80% in every fit), form about 10-15%, and the temperature wants to be slightly lower (0.5; sharper favorites) but 0.6 is inside the range. What is not stable: star availability goes to zero in champion fits yet stays at 5-10% in series fits, rotation availability swings between 0 and 10%, and the series fits want a much higher T (1.0 to 1.5, which is a different quantity: one series, not a title). The series-based weights (more star power, less net rating) disagree with the champion-based ones. With results this dependent on the objective and resample, the in-sample gains (0.09 for kappa 1) are largely overfitting: the out-of-sample gain is a small fraction of that.

## 7. Penalties (sensitivity only, not changed)

In-sample champion log loss with the current weights and T=0.6, scaling the two-way penalty (rows, 1.0 = live 0.5) and the late-season gate (columns, 1.0 = live 1.5):

| two-way x | late x0.0 | late x0.5 | late x1.0 | late x1.5 |
|---|---|---|---|---|
| 0.0 | 2.137 | 1.966 | 1.91 | 1.894 |
| 0.5 | 2.088 | 1.947 | 1.902 | 1.889 |
| 1.0 | 2.062 | 1.944 | 1.907 | 1.896 |
| 2.0 | 2.059 | 1.967 | 1.939 | 1.931 |

The late-season gate clearly helps (2.06 without it, 1.91 with it) and the live values sit near the best cell. The two-way penalty is nearly flat. No change suggested.

## 8. Recommendation

- **Keep the current weights and temperature.** The best honest out-of-sample gain from refitting is 0.016 (leave-one-out) to 0.038 (earlier-to-later) log loss with standard errors of 0.046 and 0.030, and the earlier-to-later fit beat the live model in only 6 of 16 seasons. That is not a clear improvement, and the fitted weights change with the objective. The bar for changing was a gain of at least 0.05 log loss, more than 1.5 standard errors, in both schemes; neither is met.
- The one consistent hint is that T around 0.5 is slightly better than 0.6 (re-fitting only T improves 1.907 to 1.892), and that the star-availability input as built (games played) does not help. Revisit both after the Phase 7 playoff simulation, when more data per season (series, home court) will give a stronger test. Real injury data would be needed for availability to mean "healthy in the playoffs".
- Worth testing next: add home-court advantage and seed, since record alone beat the composite at series level.
- The hunch can be updated to: the champion is almost always top 8 in net rating (top 6 in the last 10 years) and a top-3 seed by record.
