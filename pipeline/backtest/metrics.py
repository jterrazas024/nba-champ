"""Scoring a forecast of the champion: rank, probability, log loss, Brier, and hit rates."""
import math

import numpy as np


def champion_scores(probs: np.ndarray, champion: int) -> dict[str, float]:
    """Return the champion's rank (1 = our favorite; a tie counts against us), its probability, log loss (-ln p) and the multi-team Brier score (sum of squared errors over
    all teams, where the champion's target is 1 and every other team's is 0)."""
    p = float(probs[champion])
    target = np.zeros_like(probs)
    target[champion] = 1.0
    return {
        "rank": int((probs >= p).sum()),
        "prob": p,
        "log_loss": -math.log(max(p, 1e-12)),
        "brier": float(((probs - target) ** 2).sum()),
    }


def summarize(rows: list[dict[str, float]]) -> dict[str, float]:
    """Average a list of champion_scores() dicts and add top-1/3/5 hit rates and the median rank."""
    n = len(rows)
    ranks = np.array([r["rank"] for r in rows])
    return {
        "log_loss": sum(r["log_loss"] for r in rows) / n,
        "brier": sum(r["brier"] for r in rows) / n,
        "mean_prob": sum(r["prob"] for r in rows) / n,
        "top1": float((ranks <= 1).mean()),
        "top3": float((ranks <= 3).mean()),
        "top5": float((ranks <= 5).mean()),
        "median_rank": float(np.median(ranks)),
        "n": n,
    }


def uniform_probs(n_teams: int) -> np.ndarray:
    """Return equal probabilities for n_teams teams (the 'know nothing' baseline)."""
    return np.full(n_teams, 1.0 / n_teams)
