"""Run the backtest on the cached historical pages and print the tables for docs/backtest-report.md.

No network. Needs the pages fetched by pipeline/backtest/fetch_history.py.
Run:  python pipeline/backtest/run_backtest.py [--bootstrap 300]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline import config  # noqa: E402
from pipeline.backtest import fit, history, metrics  # noqa: E402
from pipeline.backtest.scoring import INPUTS, SeasonInputs, season_inputs  # noqa: E402

SHORT = {"net_rating": "NetRtg", "star_power": "Star", "recent_form": "Form",
         "star_availability": "StarAvail", "rotation_availability": "RotAvail"}


def load_all(first: int, last: int) -> tuple[list[history.SeasonData], list[SeasonInputs]]:
    """Load and compute inputs for every season in the range."""
    data = [history.load_season(y) for y in range(first, last + 1)]
    return data, [season_inputs(d) for d in data]


def rank_of(values: pd.Series, team: str, high_is_good: bool = True) -> int:
    """Rank of `team` among the values (1 = best)."""
    return int(values.rank(ascending=not high_is_good, method="min")[team])


def champion_profiles(data: list[history.SeasonData], inputs: list[SeasonInputs]) -> pd.DataFrame:
    """One row per season: where the champion ranked on each measure at the end of the regular season."""
    rows = []
    for d, si in zip(data, inputs):
        c = d.champion
        r = d.ratings.set_index("team_id")
        seeds = history.seeds_by_record(d.ratings, d.conferences).set_index("team_id")
        win_pct = r["w"] / (r["w"] + r["l"])
        rows.append({
            "year": d.end_year, "champion": c, "teams": len(r),
            "nrtg_rank": rank_of(r["nrtg"], c), "adj_nrtg_rank": rank_of(si.values["net_rating"], c),
            "ortg_rank": rank_of(r["ortg"], c), "drtg_rank": rank_of(r["drtg"], c, False),
            "seed": int(seeds.loc[c, "seed"]), "conf": seeds.loc[c, "conference"],
            "wins": int(r.loc[c, "w"]), "win_rank": rank_of(win_pct, c),
            "star_bpm": round(float(si.values.loc[c, "star_power"]), 1),
            "star_rank": rank_of(si.values["star_power"], c),
            "star_avail": round(float(si.values.loc[c, "star_availability"]), 2),
            "avail_rank": rank_of(si.values["star_availability"], c),
        })
    return pd.DataFrame(rows)


def md_table(frame: pd.DataFrame) -> str:
    """Render a DataFrame as a markdown table."""
    head = "| " + " | ".join(frame.columns) + " |\n|" + "---|" * len(frame.columns) + "\n"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in frame.itertuples(index=False))
    return head + body


def softmax_probs(score: np.ndarray, n: int, temperature: float) -> np.ndarray:
    """Softmax over the first n entries."""
    s = score[:n]
    e = np.exp((s - s.max()) / temperature)
    return e / e.sum()


def single_input_tensors(t: fit.Tensors, matrix: np.ndarray) -> fit.Tensors:
    """A copy of the tensors whose only input is `matrix` (S, 30), with no penalties (for baselines)."""
    z = matrix[..., None]
    return fit.Tensors(t.years, z, np.ones_like(z), np.zeros_like(t.penalty), t.valid, t.champ, t.series)


def zrows(matrix: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Z-score each season's row over its real teams."""
    out = np.zeros_like(matrix)
    for s in range(len(matrix)):
        v = matrix[s, valid[s]]
        out[s, valid[s]] = (v - v.mean()) / v.std()
    return out


def loso_temperature(lp: np.ndarray, test: list[int], train_for: dict[int, list[int]]) -> dict[int, int]:
    """For each test season pick the temperature index with the best training log-likelihood. lp is (nT, S)."""
    return {s: int(np.argmax(lp[:, train_for[s]].sum(axis=1))) for s in test}


def folds(n: int, scheme: str) -> tuple[list[int], dict[int, list[int]]]:
    """Return (test seasons, {season: training seasons}) for 'loso' or 'expanding' (first 10 seasons only train)."""
    if scheme == "loso":
        return list(range(n)), {s: [x for x in range(n) if x != s] for s in range(n)}
    return list(range(10, n)), {s: list(range(s)) for s in range(10, n)}


def cv_models(t: fit.Tensors, grid: fit.Grid, data_win: np.ndarray, scheme: str, objective: str = "champ"):
    """Out-of-sample champion scores for every model under one CV scheme. Returns {name: summary}, {name: rows}."""
    test, train_for = folds(len(t.years), scheme)
    n_teams = t.valid.sum(axis=1)
    out: dict[str, list[dict]] = {k: [] for k in
                                  ("Uniform", "Win% only", "Net rating only", "Current (as live)",
                                   "Current weights, T refit", "Fitted (regularized)",
                                   "Fitted on series (T for champion refit)")}
    kappas, picks = {}, {}

    nr_t = single_input_tensors(t, t.z[..., 0])
    win_t = single_input_tensors(t, zrows(data_win, t.valid))
    lp_nr = fit.champion_logprob(fit.scores(nr_t, np.array([[1.0]])), nr_t, fit.TEMPERATURES)[0]
    lp_win = fit.champion_logprob(fit.scores(win_t, np.array([[1.0]])), win_t, fit.TEMPERATURES)[0]
    lp_cur_t = grid.champ[0]
    nr_score = fit.scores(nr_t, np.array([[1.0]]))[0]
    win_score = fit.scores(win_t, np.array([[1.0]]))[0]
    cur_score = fit.scores(t, fit.current_weights()[None])[0]
    nr_pick = loso_temperature(lp_nr, test, train_for)
    win_pick = loso_temperature(lp_win, test, train_for)
    cur_pick = loso_temperature(lp_cur_t, test, train_for)

    for s in test:
        n, c = int(n_teams[s]), int(t.champ[s])
        out["Uniform"].append(metrics.champion_scores(metrics.uniform_probs(n), c))
        out["Win% only"].append(metrics.champion_scores(softmax_probs(win_score[s], n, fit.TEMPERATURES[win_pick[s]]), c))
        out["Net rating only"].append(metrics.champion_scores(softmax_probs(nr_score[s], n, fit.TEMPERATURES[nr_pick[s]]), c))
        out["Current (as live)"].append(metrics.champion_scores(softmax_probs(cur_score[s], n, config.TEMPERATURE), c))
        out["Current weights, T refit"].append(metrics.champion_scores(softmax_probs(cur_score[s], n, fit.TEMPERATURES[cur_pick[s]]), c))
        kappa = fit.choose_kappa(grid, train_for[s], objective)
        k, ti = fit.fit(grid, train_for[s], kappa, objective)
        kappas[s], picks[s] = kappa, (k, ti)
        probs = fit.season_probs(t, s, grid.weights[k], float(fit.TEMPERATURES[ti]))
        out["Fitted (regularized)"].append(metrics.champion_scores(probs, c))
        kappa_s = fit.choose_kappa(grid, train_for[s], "series")
        k_s, _ = fit.fit(grid, train_for[s], kappa_s, "series")
        t_s = int(np.argmax(grid.champ[k_s][:, train_for[s]].sum(axis=1)))
        probs_s = fit.season_probs(t, s, grid.weights[k_s], float(fit.TEMPERATURES[t_s]))
        out["Fitted on series (T for champion refit)"].append(metrics.champion_scores(probs_s, c))
    return {k: metrics.summarize(v) for k, v in out.items()}, out, kappas, picks, test


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--first", type=int, default=history.FIRST_SEASON)
    parser.add_argument("--last", type=int, default=history.LAST_SEASON)
    parser.add_argument("--bootstrap", type=int, default=300)
    args = parser.parse_args()

    data, inputs = load_all(args.first, args.last)
    series_by_year = {d.end_year: [(s.winner, s.loser) for s in d.series] for d in data}
    t = fit.build_tensors(inputs, series_by_year)
    win = np.zeros(t.valid.shape)
    for s, d in enumerate(data):
        r = d.ratings.set_index("team_id").loc[list(inputs[s].z.index)]
        win[s, :len(r)] = (r["w"] / (r["w"] + r["l"])).to_numpy()
    grid = fit.evaluate_grid(t)
    years = t.years

    print("## Data checks\n")
    checks = []
    for d, si in zip(data, inputs):
        counts = history.team_game_counts(d.games)
        checks.append({"season": d.end_year, "teams": len(d.ratings), "games_per_team": int(d.ratings["games"].max()),
                       "missing_inputs": int(si.values.isna().sum().sum()), "teams_under_20_form_games": int((counts < config.FORM_GAMES).sum()),
                       "unmapped_conf": int(len(d.ratings) - len(d.conferences)), "series": len(d.series)})
    print(md_table(pd.DataFrame(checks)), "\n")

    print("## Champion profiles\n")
    prof = champion_profiles(data, inputs)
    print(md_table(prof), "\n")
    for label, sub in (("last 10", prof[prof.year >= args.last - 9]), ("all", prof)):
        print(f"- {label} ({len(sub)} seasons): NetRtg rank<=7: {(sub.nrtg_rank <= 7).sum()}, adj<=7: {(sub.adj_nrtg_rank <= 7).sum()}, "
              f"seed<=5: {(sub.seed <= 5).sum()}, both(adj): {((sub.adj_nrtg_rank <= 7) & (sub.seed <= 5)).sum()}, "
              f"NetRtg top3: {(sub.nrtg_rank <= 3).sum()}, seed 1: {(sub.seed == 1).sum()}, "
              f"ortg<=10 & drtg<=10: {((sub.ortg_rank <= 10) & (sub.drtg_rank <= 10)).sum()}, "
              f"star rank<=5: {(sub.star_rank <= 5).sum()}, max NetRtg rank {sub.nrtg_rank.max()}, max seed {sub.seed.max()}")
    print()

    print("## Per-season results, current model (T=%.2f)\n" % config.TEMPERATURE)
    rows = []
    for s, (d, si) in enumerate(zip(data, inputs)):
        p = fit.season_probs(t, s, fit.current_weights(), config.TEMPERATURE)
        m = metrics.champion_scores(p, int(t.champ[s]))
        fav = si.z.index[int(np.argmax(p))]
        rows.append({"season": d.end_year, "champion": d.champion, "rank": m["rank"], "prob_%": round(100 * m["prob"], 1),
                     "log_loss": round(m["log_loss"], 2), "brier": round(m["brier"], 3),
                     "our_favorite": fav, "fav_prob_%": round(100 * float(p.max()), 1)})
    print(md_table(pd.DataFrame(rows)), "\n")

    # sanity: the numpy path equals the live model code
    from pipeline.backtest.scoring import probabilities
    live = probabilities(inputs[-1]).to_numpy()
    mine = fit.season_probs(t, len(years) - 1, fit.current_weights(), config.TEMPERATURE)
    print(f"(check: numpy vs live model max abs diff on {years[-1]}: {np.abs(live - mine).max():.2e})\n")

    for scheme in ("loso", "expanding"):
        summ, rows_by_model, kappas, picks, test = cv_models(t, grid, win, scheme)
        title = "Leave-one-season-out" if scheme == "loso" else f"Train on earlier seasons, test {years[test[0]]}-{years[test[-1]]}"
        print(f"## Out-of-sample champion metrics: {title} (n={len(test)})\n")
        print(md_table(pd.DataFrame([{"model": k, "log_loss": round(v["log_loss"], 3), "brier": round(v["brier"], 3),
                                      "mean_p_champ_%": round(100 * v["mean_prob"], 1), "top1": round(v["top1"], 2),
                                      "top3": round(v["top3"], 2), "top5": round(v["top5"], 2),
                                      "median_rank": v["median_rank"]} for k, v in summ.items()])), "\n")
        base = np.array([r["log_loss"] for r in rows_by_model["Current (as live)"]])
        alt = np.array([r["log_loss"] for r in rows_by_model["Fitted (regularized)"]])
        diff = alt - base
        print(f"Fitted minus current log loss per season: mean {diff.mean():+.3f}, s.e. {diff.std(ddof=1) / np.sqrt(len(diff)):.3f}, "
              f"seasons better {int((diff < 0).sum())}/{len(diff)}")
        print("kappa chosen per fold:", sorted(set(kappas.values())), "counts", {k: list(kappas.values()).count(k) for k in sorted(set(kappas.values()))})
        ws = np.array([grid.weights[k] for k, _ in picks.values()])
        ts = np.array([fit.TEMPERATURES[ti] for _, ti in picks.values()])
        print("mean fitted weights across folds:", {SHORT[k]: round(float(v), 3) for k, v in zip(INPUTS, ws.mean(axis=0))},
              "T mean %.2f range %.2f-%.2f" % (ts.mean(), ts.min(), ts.max()), "\n")

    # fits on all seasons
    all_idx = list(range(len(years)))
    print("## Fits on all seasons\n")
    cur_ll = grid.champ[0, list(fit.TEMPERATURES).index(0.6), :].mean()
    print(f"In-sample mean champion log-likelihood, current model: {cur_ll:.3f} (log loss {-cur_ll:.3f})")
    for objective in ("champ", "series"):
        kappa = fit.choose_kappa(grid, all_idx, objective)
        print(f"\nobjective={objective}: nested-chosen kappa on all seasons = {kappa}")
        for kp in (0.1, 0.3, 1.0, 3.0, 10.0):
            k, ti = fit.fit(grid, all_idx, kp, objective)
            w = grid.weights[k]
            print(f"  kappa={kp:<5} T={fit.TEMPERATURES[ti]:.2f} weights=" + ", ".join(f"{SHORT[n]} {x:.2f}" for n, x in zip(INPUTS, w)) +
                  f"  in-sample champ log loss {-grid.champ[k, ti].mean():.3f}")
    rng = np.random.default_rng(0)
    for objective in ("champ", "series"):
        for kp in (1.0, 0.3):
            fits_w, fits_t = [], []
            for _ in range(args.bootstrap):
                sample = list(rng.integers(0, len(years), len(years)))
                k, ti = fit.fit(grid, sample, kp, objective)
                fits_w.append(grid.weights[k]); fits_t.append(fit.TEMPERATURES[ti])
            fw = np.array(fits_w)
            print(f"\nBootstrap ({args.bootstrap} resamples of seasons), objective={objective}, kappa={kp}:")
            for i, name in enumerate(INPUTS):
                lo, hi = np.percentile(fw[:, i], [10, 90])
                print(f"  {SHORT[name]:<10} current {fit.current_weights()[i]:.3f}  mean {fw[:, i].mean():.3f}  10-90%: {lo:.2f}-{hi:.2f}")
            lo, hi = np.percentile(fits_t, [10, 90])
            print(f"  T mean {np.mean(fits_t):.2f} 10-90%: {lo:.2f}-{hi:.2f}")

    # series-level out-of-sample
    print("\n## Series-level (log loss per series, leave-one-season-out)\n")
    test, train_for = folds(len(years), "loso")
    n_ser = grid.n_series
    nr_t = single_input_tensors(t, t.z[..., 0])
    ser_nr = fit.series_logprob(fit.scores(nr_t, np.array([[1.0]])), nr_t, fit.TEMPERATURES)[0]
    win_t = single_input_tensors(t, zrows(win, t.valid))
    ser_win = fit.series_logprob(fit.scores(win_t, np.array([[1.0]])), win_t, fit.TEMPERATURES)[0]
    ser_cur = grid.series[0]
    def loso_series(lp):  # lp (nT, S)
        total = 0.0
        for s in test:
            ti = int(np.argmax(lp[:, train_for[s]].sum(axis=1)))
            total += lp[ti, s]
        return -total / n_ser.sum()
    res = {"Coin flip": float(np.log(2)), "Win% only (T refit)": loso_series(ser_win),
           "Net rating only (T refit)": loso_series(ser_nr), "Current weights (T refit)": loso_series(ser_cur)}
    total = 0.0
    for s in test:
        kappa = fit.choose_kappa(grid, train_for[s], "series")
        k, ti = fit.fit(grid, train_for[s], kappa, "series")
        total += grid.series[k, ti, s]
    res["Series-fitted regularized"] = -total / n_ser.sum()
    print(md_table(pd.DataFrame([{"model": k, "log_loss_per_series": round(v, 4)} for k, v in res.items()])), f"\n(series: {int(n_ser.sum())})\n")

    # penalty sensitivity (current weights, T=0.6)
    print("## Penalty sensitivity (current weights, T=0.6, in-sample champion log loss)\n")
    two_flag = np.isin(np.round(t.penalty, 3), [-0.5, -2.0]).astype(float)
    late_flag = np.isin(np.round(t.penalty, 3), [-1.5, -2.0]).astype(float)
    base_scores = fit.scores(fit.Tensors(t.years, t.z, t.present, np.zeros_like(t.penalty), t.valid, t.champ, t.series),
                             fit.current_weights()[None])
    table = []
    for a in (0.0, 0.5, 1.0, 2.0):
        row = {"two-way x": a}
        for b in (0.0, 0.5, 1.0, 1.5):
            sc = base_scores + (-a * config.TWO_WAY_PENALTY * two_flag - b * config.LATE_GATE_PENALTY * late_flag)[None]
            lp = fit.champion_logprob(sc, t, np.array([config.TEMPERATURE]))[0, 0]
            row[f"late x{b}"] = round(float(-lp.mean()), 3)
        table.append(row)
    print(md_table(pd.DataFrame(table)), "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
