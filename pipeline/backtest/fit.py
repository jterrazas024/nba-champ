"""Fit the model's weights and temperature on past seasons, and judge them out of sample.

With only ~25 champions a free fit would overfit, so the fit is regularized: every candidate
(a weight vector plus a temperature) is scored by the champion's log-likelihood on the training
seasons minus a Gaussian penalty for straying from the current weights and temperature
(strength `kappa`; a larger kappa stays closer to the current model). Candidates come from a coarse grid,
which keeps the search honest and easy to follow. Everything is judged on seasons the fit never
saw (leave-one-season-out, or train on earlier seasons and test on later ones), and `kappa` itself
is chosen inside each training set (nested cross-validation).

Series-level scoring is a second, larger sample: P(team A beats B) = logistic((score_A - score_B)/T).
"""
from dataclasses import dataclass
from itertools import product

import numpy as np

from pipeline import config
from pipeline.backtest.scoring import INPUTS, SeasonInputs

MAX_TEAMS = 30
STEP = 0.05                                    # weight grid step (5 percentage points)
TEMPERATURES = np.array([0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.5, 2.0])
SIGMA_W = 0.10                                 # prior spread of each weight at kappa = 1
SIGMA_LOG_T = 0.5                              # prior spread of log(temperature) at kappa = 1
KAPPAS = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)


@dataclass
class Tensors:
    """All seasons stacked into arrays (teams padded to 30)."""

    years: list[int]
    z: np.ndarray            # (S, 30, 5) z-scores, NaN replaced by 0
    present: np.ndarray      # (S, 30, 5) 1.0 where the input exists
    penalty: np.ndarray      # (S, 30)
    valid: np.ndarray        # (S, 30) True for real teams
    champ: np.ndarray        # (S,) index of the champion
    series: np.ndarray       # (N, 3) season index, winner index, loser index


def build_tensors(seasons: list[SeasonInputs], series_by_year: dict[int, list[tuple[str, str]]]) -> Tensors:
    """Stack SeasonInputs into arrays. `series_by_year[y]` lists (winner_id, loser_id) pairs."""
    count = len(seasons)
    z = np.zeros((count, MAX_TEAMS, len(INPUTS)))
    present = np.zeros_like(z)
    penalty = np.zeros((count, MAX_TEAMS))
    valid = np.zeros((count, MAX_TEAMS), dtype=bool)
    champ = np.zeros(count, dtype=int)
    rows = []
    for s, season in enumerate(seasons):
        ids = list(season.z.index)
        index = {team: i for i, team in enumerate(ids)}
        values = season.z.to_numpy()
        z[s, :len(ids)] = np.nan_to_num(values)
        present[s, :len(ids)] = ~np.isnan(values)
        penalty[s, :len(ids)] = season.penalty.fillna(0.0).to_numpy()
        valid[s, :len(ids)] = True
        champ[s] = index[season.champion]
        for winner, loser in series_by_year.get(season.end_year, []):
            if winner in index and loser in index:
                rows.append((s, index[winner], index[loser]))
    return Tensors([s.end_year for s in seasons], z, present, penalty, valid, champ,
                   np.array(rows, dtype=int).reshape(-1, 3))


def weight_grid(step: float = STEP) -> np.ndarray:
    """Return every weight vector over the 5 inputs in multiples of `step` that sums to 1."""
    units = round(1 / step)
    rows = [c + (units - sum(c),) for c in product(range(units + 1), repeat=len(INPUTS) - 1) if sum(c) <= units]
    return np.array(rows, dtype=float) / units


def current_weights() -> np.ndarray:
    """Return the live model's weights as an array in INPUTS order."""
    return np.array([config.WEIGHTS[k] for k in INPUTS])


def scores(t: Tensors, weights: np.ndarray, penalty_scale: float = 1.0) -> np.ndarray:
    """Return composite + penalty, shape (K, S, 30), for K weight vectors (renormalizing missing inputs)."""
    numerator = np.einsum("kj,stj->kst", weights, t.z)
    denominator = np.einsum("kj,stj->kst", weights, t.present)
    composite = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
    return composite + penalty_scale * t.penalty[None]


def champion_logprob(score: np.ndarray, t: Tensors, temperature: np.ndarray) -> np.ndarray:
    """Return log P(champion), shape (K, nT, S), for scores (K, S, 30) and each temperature."""
    scaled = score[:, None] / temperature[None, :, None, None]                  # (K, nT, S, 30)
    scaled = np.where(t.valid[None, None], scaled, -np.inf)
    top = scaled.max(axis=-1, keepdims=True)
    log_total = (top[..., 0] + np.log(np.exp(scaled - top).sum(axis=-1)))
    chosen = np.take_along_axis(scaled, t.champ[None, None, :, None], axis=-1)[..., 0]
    return chosen - log_total


def series_logprob(score: np.ndarray, t: Tensors, temperature: np.ndarray) -> np.ndarray:
    """Return summed log P(series winner) per season, shape (K, nT, S)."""
    out = np.zeros((score.shape[0], len(temperature), len(t.years)))
    if not len(t.series):
        return out
    season, win, lose = t.series.T
    diff = score[:, season, win] - score[:, season, lose]                        # (K, N)
    logp = -np.logaddexp(0.0, -diff[:, None, :] / temperature[None, :, None])    # (K, nT, N)
    for s in range(len(t.years)):
        out[:, :, s] = logp[:, :, season == s].sum(axis=-1)
    return out


@dataclass
class Grid:
    """Log-likelihood of every candidate (weights x temperature) on every season."""

    weights: np.ndarray       # (K, 5)
    temperatures: np.ndarray  # (nT,)
    champ: np.ndarray         # (K, nT, S) log P(champion)
    series: np.ndarray        # (K, nT, S) summed log P(series winner)
    n_series: np.ndarray      # (S,) number of series per season


def evaluate_grid(t: Tensors, weights: np.ndarray | None = None, chunk: int = 500) -> Grid:
    """Score the weight grid (plus the current weights as the first candidate) on all seasons."""
    weights = np.vstack([current_weights(), weight_grid()]) if weights is None else weights
    champ, series = [], []
    for start in range(0, len(weights), chunk):
        s = scores(t, weights[start:start + chunk])
        champ.append(champion_logprob(s, t, TEMPERATURES))
        series.append(series_logprob(s, t, TEMPERATURES))
    n_series = np.bincount(t.series[:, 0], minlength=len(t.years)) if len(t.series) else np.zeros(len(t.years))
    return Grid(weights, TEMPERATURES, np.concatenate(champ), np.concatenate(series), n_series)


def prior_penalty(grid: Grid, kappa: float) -> np.ndarray:
    """Return the Gaussian-prior penalty (nats), shape (K, nT): distance from the current weights and 0.6."""
    w0 = current_weights()
    pw = ((grid.weights - w0) ** 2).sum(axis=1) / (2 * SIGMA_W ** 2)
    pt = (np.log(grid.temperatures / config.TEMPERATURE) ** 2) / (2 * SIGMA_LOG_T ** 2)
    return kappa * (pw[:, None] + pt[None, :])


def fit(grid: Grid, train: list[int], kappa: float, objective: str = "champ") -> tuple[int, int]:
    """Return (weight index, temperature index) maximizing training log-likelihood minus the prior penalty.

    objective "champ" sums the log-probability of each champion (one observation per season);
    "series" sums the log-probability of every series winner (about 15 observations per season).
    """
    total = (grid.champ if objective == "champ" else grid.series)[:, :, train].sum(axis=-1)
    objective_value = total - prior_penalty(grid, kappa)
    flat = int(np.argmax(objective_value))
    return divmod(flat, len(grid.temperatures))


def choose_kappa(grid: Grid, train: list[int], objective: str = "champ") -> float:
    """Pick kappa by leave-one-season-out inside the training seasons (higher out-of-sample log-likelihood wins)."""
    best, best_value = KAPPAS[0], -np.inf
    for kappa in KAPPAS:
        value = 0.0
        for held in train:
            rest = [s for s in train if s != held]
            k, ti = fit(grid, rest, kappa, objective)
            value += (grid.champ if objective == "champ" else grid.series)[k, ti, held]
        if value > best_value:
            best, best_value = kappa, value
    return best


def season_probs(t: Tensors, season: int, weights: np.ndarray, temperature: float,
                 penalty_scale: float = 1.0) -> np.ndarray:
    """Return title probabilities for the real teams of one season (numpy twin of scoring.probabilities)."""
    s = scores(t, weights[None], penalty_scale)[0, season]
    n = int(t.valid[season].sum())
    e = np.exp((s[:n] - s[:n].max()) / temperature)
    return e / e.sum()
