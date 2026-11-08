"""From inputs to odds (SPEC.md section 6.3): z-scores, composite score, penalties, softmax.

Pure functions on pandas Series/DataFrames indexed by team_id. No network and no files, so every
step can be checked by hand. Tunable values (weights, penalties, temperature) live in
pipeline/config.py.
"""
import math
from datetime import date

import pandas as pd

from pipeline import config


def ordinal(n: int) -> str:
    """1 -> "1st", 22 -> "22nd", 13 -> "13th"."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def zscores(values: pd.Series) -> pd.Series:
    """Return each team's z-score: (value - mean) / standard deviation across the teams.

    Uses the population standard deviation. Teams with no value (NaN) stay NaN and are left out
    of the mean and deviation. If every team has the same value, all z-scores are 0.
    """
    mean = values.mean()
    spread = values.std(ddof=0)
    if pd.isna(spread) or spread == 0:
        return values.where(values.isna(), 0.0)
    return (values - mean) / spread


def percentiles(values: pd.Series) -> pd.Series:
    """Return each team's league percentile from 0 (lowest value) to 100 (highest), rounded.

    Tied values share their average rank. NaN stays NaN. With one team the percentile is 50.
    """
    count = values.notna().sum()
    if count < 2:
        return values.where(values.isna(), 50.0)
    ranks = values.rank(method="average")  # 1 = lowest
    return ((ranks - 1) / (count - 1) * 100).round()


def composite_scores(
    z_by_input: dict[str, pd.Series], weights: dict[str, float] = config.WEIGHTS
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    """Combine z-scores into one composite score per team (SPEC 6.3 steps 4).

    composite = sum over inputs of (effective weight x z-score). A team missing an input has
    that input dropped and its remaining weights renormalized to sum to 1, rather than getting
    a made-up value. Inputs with weight 0 are ignored.

    Returns (composite, contributions, effective_weights); the last two have one row per team and
    one column per used input. A team with no usable input gets a composite of NaN.
    """
    used = [k for k, w in weights.items() if w > 0 and k in z_by_input]
    z = pd.DataFrame({k: z_by_input[k] for k in used})
    raw_weight = z.notna() * pd.Series({k: weights[k] for k in used})
    effective = raw_weight.div(raw_weight.sum(axis=1).replace(0, float("nan")), axis=0)
    contributions = effective * z.fillna(0.0)
    composite = contributions.sum(axis=1).where(effective.notna().any(axis=1))
    return composite, contributions, effective.fillna(0.0)


def _rank_best_first(values: pd.Series, higher_is_better: bool) -> pd.Series:
    """Rank teams 1 (best) to N; ties share the better rank."""
    return values.rank(ascending=not higher_is_better, method="min").astype(int)


def penalties(
    ratings: pd.DataFrame, blended_net: pd.Series, today: date, season_end_year: int
) -> tuple[pd.Series, dict[str, list[str]]]:
    """Return (amounts, notes): the penalty to subtract per team (0 or negative) and a note each.

    Two-way balance (SPEC 6.2): offense or defense ranked worse than TWO_WAY_RANK_CUTOFF costs
    TWO_WAY_PENALTY, once, and only after TWO_WAY_MIN_GAMES games. Late-season gate: from
    LATE_GATE_START onward, a net rating (blended) ranked worse than LATE_GATE_RANK_CUTOFF costs
    LATE_GATE_PENALTY. `ratings` needs team_id, games, ortg, drtg.
    """
    table = ratings.set_index("team_id")
    amounts = pd.Series(0.0, index=table.index)
    notes: dict[str, list[str]] = {team: [] for team in table.index}

    offense = _rank_best_first(table["ortg"], higher_is_better=True)
    defense = _rank_best_first(table["drtg"], higher_is_better=False)  # fewer points allowed is better
    for team in table.index:
        if table.loc[team, "games"] < config.TWO_WAY_MIN_GAMES:
            continue
        weak = []
        if offense[team] > config.TWO_WAY_RANK_CUTOFF:
            weak.append(f"offense ranks {ordinal(offense[team])}")
        if defense[team] > config.TWO_WAY_RANK_CUTOFF:
            weak.append(f"defense ranks {ordinal(defense[team])}")
        if weak:
            amounts[team] -= config.TWO_WAY_PENALTY
            notes[team].append(f"{' and '.join(weak).capitalize()}: -{config.TWO_WAY_PENALTY}")

    month, day = config.LATE_GATE_START
    if today >= date(season_end_year, month, day):
        net_rank = _rank_best_first(blended_net.reindex(table.index), higher_is_better=True)
        for team in table.index:
            if net_rank[team] > config.LATE_GATE_RANK_CUTOFF:
                amounts[team] -= config.LATE_GATE_PENALTY
                notes[team].append(f"Net rating ranks {ordinal(net_rank[team])} late in the season: -{config.LATE_GATE_PENALTY}")
    return amounts, notes


def softmax(scores: pd.Series, temperature: float = config.TEMPERATURE) -> pd.Series:
    """Turn composite scores into probabilities that sum to 1: exp(score / T) / sum of the same.

    A lower temperature gives the top teams a larger share. The maximum is subtracted first so
    large scores cannot overflow; this does not change the result.
    """
    shifted = (scores - scores.max()) / temperature
    weights = shifted.map(math.exp)
    return weights / weights.sum()


def round_probs(probs: pd.Series, decimals: int = config.PROB_DECIMALS) -> pd.Series:
    """Round probabilities to `decimals` places so that they sum to exactly 1.

    Plain rounding can leave the total slightly off 1. Instead every probability is rounded
    down, and the few units left over go to the teams with the largest rounded-off remainders.
    """
    scale = 10 ** decimals
    units = (probs * scale).map(math.floor).astype(int)
    leftover = scale - int(units.sum())
    remainders = (probs * scale - units).sort_values(ascending=False, kind="stable")
    units[remainders.index[:leftover]] += 1
    return units / scale


def rank_teams(probs: pd.Series, composite: pd.Series) -> pd.Series:
    """Return ranks 1..N: highest probability first, ties broken by composite score, then team id."""
    order = pd.DataFrame({"p": probs, "c": composite, "id": probs.index}).sort_values(
        ["p", "c", "id"], ascending=[False, False, True], kind="stable")
    return pd.Series(range(1, len(order) + 1), index=order.index).reindex(probs.index)
