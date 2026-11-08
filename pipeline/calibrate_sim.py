"""Offline calibration and scoring of the playoff simulation (SPEC.md section 6.4). Never run by the daily build.

Two jobs, both read only cached pages (no network):
  (a) pick MARGIN_PER_Z so the simulation's title probabilities on a recent day's real data are
      close to the current softmax's, and report how the two methods compare;
  (b) score the simulation against the softmax on the backtest seasons (champion log loss, Brier,
      rank, top-k hit rates) with the simulation's constants chosen leave-one-season-out.

Run:  python pipeline/calibrate_sim.py [--day 2026-11-08] [--skip-backtest] [--runs 20000]
Output is markdown for docs/simulation-report.md.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import build_odds, config, model, sim, sim_inputs  # noqa: E402

MARGIN_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0)
PLAYIN_FIRST_SEASON = 2021     # seasons ending in 2021 or later used the play-in


# ---------- today's real data, read from the cache ----------

class OfflineClient:
    """Stands in for the fetch client: serves cached pages from data/raw/<day>/ and never touches the network."""

    def __init__(self, day_dir: Path):
        self.day_dir = day_dir
        self.requests_made = 0

    def get_html(self, path: str, cache_name: str) -> str:
        page = self.day_dir / f"{cache_name}.html"
        if not page.exists():
            raise FileNotFoundError(f"{page} is not cached; this script never fetches")
        return page.read_text(encoding="utf-8", errors="replace")


def fill_schedule(known: pd.DataFrame, need: pd.Series, rng: np.random.Generator) -> pd.DataFrame:
    """Return `known` (home_id, away_id) plus made-up games so every team has `need[team]` more games in total.

    The made-up games pair random teams (no team plays itself; home and away are a coin flip). It
    stands in for months of the schedule we do not have cached, so opponents are an average draw
    and home/away is not balanced per team.
    """
    have = pd.concat([known["home_id"], known["away_id"]]).value_counts().reindex(need.index).fillna(0)
    extra = (need - have).astype(int)
    if (extra < 0).any() or extra.sum() % 2:
        raise ValueError("the known games already exceed a team's games left, or the leftover total is odd")
    stubs = np.repeat(np.array(extra.index), extra.to_numpy())
    rng.shuffle(stubs)
    for _ in range(10_000):                                  # repair pairs where a team meets itself
        pairs = stubs.reshape(-1, 2)
        clash = np.where(pairs[:, 0] == pairs[:, 1])[0]
        if not len(clash):
            break
        for i in clash:
            j = int(rng.integers(len(stubs)))
            stubs[2 * i], stubs[j] = stubs[j], stubs[2 * i]
    pairs = stubs.reshape(-1, 2)
    flip = rng.random(len(pairs)) < 0.5
    made_up = pd.DataFrame({"home_id": np.where(flip, pairs[:, 0], pairs[:, 1]),
                            "away_id": np.where(flip, pairs[:, 1], pairs[:, 0])})
    return pd.concat([known[["home_id", "away_id"]], made_up], ignore_index=True)


def todays_league_inputs(day: date, day_dir: Path, seed: int = 0):
    """Rebuild the real composite for `day` exactly as build_odds.build does, from cached pages.

    Returns (final, wins, games, conferences, remaining): the composite after penalties per team,
    current wins and games played, team -> "East"/"West", and the games left (known unplayed games
    from the cached monthly pages plus made-up filler for the months we do not have).
    """
    odds = build_odds.build(OfflineClient(day_dir), day, None, with_sim=False).odds
    teams = pd.DataFrame(odds["teams"]).set_index("id")
    final = teams["composite"].astype(float)
    wins = teams["record"].str.split("-").str[0].astype(float)
    games = teams["record"].map(lambda r: sum(int(x) for x in r.split("-"))).astype(float)
    conferences = teams["conference"].to_dict()
    end_year = config.season_end_year(day)
    pages = [(day_dir / f"games_{end_year}_{month}.html").read_text(encoding="utf-8", errors="replace")
             for month in build_odds.SEASON_MONTHS if (day_dir / f"games_{end_year}_{month}.html").exists()]
    known = sim_inputs.unplayed_regular_games(pages)
    remaining = fill_schedule(known, config.SEASON_GAMES - games, np.random.default_rng(seed))
    return final, wins, games, conferences, remaining


# ---------- comparing two sets of title probabilities ----------

def _ranks(values: np.ndarray) -> np.ndarray:
    return pd.Series(values).rank(method="average").to_numpy()


def compare_probs(a: pd.Series, b: pd.Series) -> dict[str, float]:
    """Compare two title-probability Series on the same teams: Spearman rank correlation, largest and
    mean absolute gap (in probability points, 0-100), and each one's favorite share."""
    b = b.reindex(a.index)
    gap = (a - b).abs()
    return {"spearman": float(np.corrcoef(_ranks(a.to_numpy()), _ranks(b.to_numpy()))[0, 1]),
            "max_gap": float(100 * gap.max()), "mean_gap": float(100 * gap.mean()),
            "favorite_a": float(100 * a.max()), "favorite_b": float(100 * b.max())}


def simulate_titles(final: pd.Series, wins: pd.Series, games: pd.Series, conferences: dict, remaining: pd.DataFrame,
                    margin: float, runs: int, seed: int, fmt: str = "playin",
                    params: sim.SimParams = sim.SimParams()) -> sim.SimResult:
    """Run the simulation with strength = margin * composite."""
    league = sim.league_from_frames(final * margin, games, wins, conferences, remaining)
    return sim.simulate(league, runs=runs, seed=seed, fmt=fmt, params=params)


def fit_margin(target: pd.Series, run_at, grid=MARGIN_GRID) -> tuple[float, pd.DataFrame]:
    """Pick the margin whose simulated title probabilities have the smallest sum of squared gaps to `target`.

    `run_at(margin)` returns a probability Series. The grid is searched, then refined in steps of
    one tenth around the best value. Returns (best margin, table of margin and squared gap).
    """
    def loss(margin: float) -> float:
        return float(((run_at(margin) - target.reindex(run_at(margin).index)) ** 2).sum())

    rows = {m: loss(m) for m in grid}
    best = min(rows, key=rows.get)
    for m in np.round(np.arange(max(best - 0.5, 0.1), best + 0.51, 0.1), 1):
        rows.setdefault(float(m), loss(float(m)))
    table = pd.DataFrame({"margin": list(rows), "sq_gap": list(rows.values())}).sort_values("margin").reset_index(drop=True)
    return float(table.loc[table["sq_gap"].idxmin(), "margin"]), table


# ---------- scoring on past seasons ----------

def loso_pick(loglik: np.ndarray) -> list[int]:
    """Leave-one-season-out choice. `loglik` is (settings, seasons) of champion log-likelihoods.

    For each season, return the index of the setting with the highest total over the OTHER seasons.
    """
    total = loglik.sum(axis=1, keepdims=True)
    return [int(np.argmax(total[:, 0] - loglik[:, s])) for s in range(loglik.shape[1])]


def champion_loglik(probs: np.ndarray, champion: int, floor: float) -> float:
    """Natural log of the champion's probability, with probabilities floored at `floor` (simulated shares can be 0)."""
    return float(np.log(max(float(probs[champion]), floor)))


def paired_difference(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Per-season difference a - b (for log loss: negative means a is better): mean, standard error, seasons a is better."""
    d = np.asarray(a) - np.asarray(b)
    return {"mean": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(len(d))), "better": int((d < 0).sum()), "n": len(d)}


def _season_league(inputs, data, strength: pd.Series):
    """Series/arrays for one past season, aligned to the order of the model's team index."""
    ratings = inputs.ratings.set_index("team_id").reindex(inputs.z.index)
    return strength, ratings["w"], ratings["w"] + ratings["l"], data.conferences


def season_strengths(inputs) -> dict[str, pd.Series]:
    """Per-team strength variants for a past season: the live composite with penalties, without, and net rating alone."""
    from pipeline.backtest import scoring
    composite, _, _ = model.composite_scores({k: inputs.z[k] for k in scoring.INPUTS})
    return {"composite": composite + inputs.penalty, "no_penalty": composite, "net_only": inputs.z["net_rating"]}


def backtest_loglik_table(seasons, strength_key: str, margins, uncertainties, runs: int):
    """Simulate every season for every (margin, uncertainty) setting.

    Returns (settings, probs) where settings is a list of (margin, uncertainty_points) and
    probs[i][s] is the title-probability array for season s under setting i.
    """
    settings = [(m, u) for u in uncertainties for m in margins]
    probs = []
    for margin, unc in settings:
        per_season = []
        for data, inputs in seasons:
            strength, wins, games, conferences = _season_league(inputs, data, season_strengths(inputs)[strength_key])
            fmt = "playin" if data.end_year >= PLAYIN_FIRST_SEASON else "top8"
            league = sim.league_from_frames(strength * margin, games, wins, conferences)
            result = sim.simulate(league, runs=runs, seed=data.end_year, fmt=fmt,
                                  params=sim.SimParams(uncertainty_points=unc))
            per_season.append(result.title)
        probs.append(per_season)
    return settings, probs


def softmax_probs(score: np.ndarray, temperature: float) -> np.ndarray:
    """Softmax of scores / temperature."""
    e = np.exp((score - score.max()) / temperature)
    return e / e.sum()


def main() -> int:
    from pipeline.backtest import history, metrics
    from pipeline.backtest.scoring import season_inputs

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--day", default="2026-11-08")
    parser.add_argument("--runs", type=int, default=config.SIM_RUNS)
    parser.add_argument("--skip-backtest", action="store_true")
    args = parser.parse_args()
    day = date.fromisoformat(args.day)

    # ---- (a) today's data ----
    final, wins, games, conferences, remaining = todays_league_inputs(day, config.CACHE_ROOT / args.day)
    soft = model.softmax(final)
    run_at = lambda m: pd.Series(simulate_titles(final, wins, games, conferences, remaining, m, args.runs, 1).title, index=final.index)  # noqa: E731
    best, table = fit_margin(soft, run_at)
    print(f"## (a) Today ({args.day}): fit of MARGIN_PER_Z to the softmax\n")
    print(f"games played per team: {games.mean():.1f}; remaining games simulated: {len(remaining)}")
    print(table.to_string(index=False), "\n")
    print(f"Best margin: {best}\n")
    sim_probs = run_at(best)
    stats = compare_probs(sim_probs, soft)
    print({k: round(v, 3) for k, v in stats.items()}, "\n")
    top = pd.DataFrame({"sim_%": (100 * sim_probs).round(1), "softmax_%": (100 * soft).round(1)}).sort_values("softmax_%", ascending=False)
    print(top.head(12).to_string(), "\n")

    if args.skip_backtest:
        return 0
    # ---- (b) backtest ----
    seasons = []
    for year in range(history.FIRST_SEASON, history.LAST_SEASON + 1):
        data = history.load_season(year)
        seasons.append((data, season_inputs(data)))
    champs = [list(inp.z.index).index(d.champion) for d, inp in seasons]
    floor = 1.0 / (2 * args.runs)
    out: dict[str, list[dict]] = {}
    ll: dict[str, np.ndarray] = {}

    def record(name: str, per_season_probs: list[np.ndarray]) -> None:
        out[name] = [metrics.champion_scores(np.maximum(p, floor), c) for p, c in zip(per_season_probs, champs)]
        ll[name] = np.array([-r["log_loss"] for r in out[name]])

    temps = np.array([0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.5, 2.0])
    for key, label in (("composite", "Softmax, live composite"), ("net_only", "Softmax, net rating only")):
        scores = [season_strengths(inp)[key].to_numpy() for _, inp in seasons]
        table_ll = np.array([[np.log(softmax_probs(sc, t)[c]) for sc, c in zip(scores, champs)] for t in temps])
        if key == "composite":
            record(label + " (T=0.6 as live)", [softmax_probs(sc, config.TEMPERATURE) for sc in scores])
        picks = loso_pick(table_ll)
        record(label + " (T refit)", [softmax_probs(sc, temps[picks[s]]) for s, sc in enumerate(scores)])
    for key, label in (("composite", "Simulation, live composite"), ("no_penalty", "Simulation, no penalties"),
                       ("net_only", "Simulation, net rating only")):
        uncs = (0.0, 3.0, 6.0) if key == "composite" else (3.0,)
        settings, probs = backtest_loglik_table(seasons, key, MARGIN_GRID, uncs, args.runs)
        table_ll = np.array([[np.log(max(p[s][c], floor)) for s, c in enumerate(champs)] for p in probs])
        picks = loso_pick(table_ll)
        record(label, [probs[picks[s]][s] for s in range(len(seasons))])
        full = table_ll.sum(axis=1)
        print(f"\n{label}: in-sample best setting (margin, uncertainty) = {settings[int(np.argmax(full))]}; "
              f"LOSO choices = {sorted(set(settings[i] for i in picks))}")

    print("\n## (b) Out-of-sample champion scores, 26 seasons\n")
    rows = {k: metrics.summarize(v) for k, v in out.items()}
    print(pd.DataFrame(rows).T[["log_loss", "brier", "mean_prob", "top1", "top3", "top5", "median_rank"]].round(3).to_string(), "\n")
    base = "Softmax, live composite (T=0.6 as live)"
    for name in out:
        if name != base:
            d = paired_difference(-ll[name], -ll[base])
            print(f"{name} minus live softmax, log loss per season: mean {d['mean']:+.3f}, s.e. {d['se']:.3f}, better in {d['better']}/{d['n']}")
    print()
    for sim_name, soft_name in (("Simulation, live composite", "Softmax, live composite (T refit)"),
                                ("Simulation, net rating only", "Softmax, net rating only (T refit)")):
        d = paired_difference(-ll[sim_name], -ll[soft_name])
        print(f"Like for like, {sim_name} minus {soft_name}: mean {d['mean']:+.3f}, s.e. {d['se']:.3f}, better in {d['better']}/{d['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
