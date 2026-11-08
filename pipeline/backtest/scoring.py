"""Run the real feature and model code on one historical season (SPEC.md sections 6.1-6.3).

This mirrors the middle of pipeline/build_odds.py (inputs -> z-scores -> composite -> penalties
-> softmax) but reads a SeasonData instead of fetching. Features and model functions are the live
ones, imported unchanged. Limits of a backtest: no injury report (so no injury factor) and the
"today" date is fixed to the end of the regular season.
"""
from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline import config, features, model
from pipeline.backtest.history import SeasonData

NO_INJURIES = pd.DataFrame({"player_id": pd.Series(dtype=str), "update": pd.Series(dtype="datetime64[ns]"),
                            "status": pd.Series(dtype=str)})
INPUTS = ("net_rating", "star_power", "recent_form", "star_availability", "rotation_availability")


@dataclass
class SeasonInputs:
    """One season's model inputs at the end of the regular season."""

    end_year: int
    values: pd.DataFrame     # raw input values, one row per team, one column per input in INPUTS
    z: pd.DataFrame          # z-scores of those values (NaN where missing)
    penalty: pd.Series       # penalties to add to the composite (0 or negative)
    ratings: pd.DataFrame
    champion: str


def season_inputs(data: SeasonData) -> SeasonInputs:
    """Compute the five model inputs for every team as they stood after the last regular-season game."""
    today = date(data.end_year, 4, 30)
    ratings = data.ratings
    games_played = features.team_games(ratings)
    roster = features.current_roster(data.seasons[0])
    stars = features.star_power(roster, games_played)
    availability = features.star_availability(
        stars, [features.season_games(s) for s in data.seasons], games_played, NO_INJURIES, today)
    values = pd.DataFrame({
        "net_rating": features.net_rating(ratings, data.prior),
        "star_power": pd.Series({t: r.value for t, r in stars.items()}, dtype=float),
        "recent_form": pd.Series(features.recent_form(data.games, list(games_played.index)), dtype=float),
        "star_availability": pd.Series({t: r.value for t, r in availability.items()}, dtype=float),
        "rotation_availability": pd.Series(features.rotation_availability(roster, games_played), dtype=float),
    })[list(INPUTS)]
    z = values.apply(model.zscores)
    penalty, _ = model.penalties(ratings, values["net_rating"], today, data.end_year)
    return SeasonInputs(data.end_year, values, z, penalty.reindex(values.index), ratings, data.champion)


def probabilities(inputs: SeasonInputs, weights: dict[str, float] = config.WEIGHTS,
                  temperature: float = config.TEMPERATURE) -> pd.Series:
    """Return each team's title probability (unrounded) using the live composite, penalty and softmax."""
    composite, _, _ = model.composite_scores({k: inputs.z[k] for k in INPUTS}, weights)
    return model.softmax(composite + inputs.penalty, temperature)
