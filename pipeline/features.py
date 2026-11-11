"""The model's inputs: one small, pure function per input (SPEC.md section 6.1).

Every function takes already-parsed tables (see pipeline/parse.py) and returns plain numbers.
There is no network access and no file access here, so each one is easy to test by hand.
Tunable values (thresholds, factors) live in pipeline/config.py.

A team with no usable data for an input gets None for that input; the model then renormalizes
that team's weights instead of inventing a number.
"""
from dataclasses import dataclass
from datetime import date

import pandas as pd

from pipeline import config


@dataclass(frozen=True)
class Star:
    """One of a team's two stars."""

    player_id: str
    name: str
    bpm: float


@dataclass(frozen=True)
class StarResult:
    """A team's star power (None if there is no usable star) and who the stars are."""

    value: float | None
    stars: tuple[Star, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AvailabilityResult:
    """A team's star availability (0 to 1, or None) and any injury notes."""

    value: float | None
    notes: tuple[str, ...] = ()


# ---------- small helpers ----------

def team_games(ratings: pd.DataFrame) -> pd.Series:
    """Return games played so far by each team, indexed by team_id (wins + losses)."""
    return ratings.set_index("team_id")["games"]


def season_games(players: pd.DataFrame) -> pd.Series:
    """Return each player's games played for a whole season, indexed by player_id.

    A player traded mid-season has a total row (all teams combined); that row is used. Everyone
    else has a single row.
    """
    has_total = players.groupby("player_id")["is_total"].transform("any")
    whole_season = players[players["is_total"] | ~has_total]
    return whole_season.groupby("player_id")["g"].first()


def current_roster(players: pd.DataFrame) -> pd.DataFrame:
    """Return one row per player: player_id, player, team_id, g, mp, bpm.

    Games, minutes and BPM are for the whole season (the total row for a traded player). The team
    is the one the player is on now: the last team in his list of stints.
    """
    rows = []
    for player_id, group in players.groupby("player_id", sort=False):
        totals = group[group["is_total"]]
        stints = group[~group["is_total"]]
        season = totals.iloc[0] if len(totals) else group.iloc[0]
        team = stints.iloc[-1]["team_id"] if len(stints) else None
        rows.append({"player_id": player_id, "player": season["player"], "team_id": team,
                     "g": season["g"], "mp": season["mp"], "bpm": season["bpm"]})
    roster = pd.DataFrame(rows, columns=["player_id", "player", "team_id", "g", "mp", "bpm"])
    return roster[roster["team_id"].notna()].reset_index(drop=True)


# ---------- net rating (SPEC 6.1, 6.3) ----------

def net_rating(ratings: pd.DataFrame, prior_ratings: pd.DataFrame | None) -> pd.Series:
    """Return each team's blended net rating, indexed by team_id.

    current = NRtg + SOS * min(1, games / SOS_FULL_AT)     (schedule strength phased in; SPEC 13)
    prior   = PRIOR_REGRESSION * (last season's NRtg + SOS, minus the league mean)
    value   = w * current + (1 - w) * prior,  with w = games / (games + PRIOR_BLEND_GAMES)

    With no prior (None) the current value is used as is.
    """
    ratings = ratings.set_index("team_id")
    games = ratings["games"]
    ramp = (games / config.SOS_FULL_AT).clip(upper=1.0)
    current = ratings["nrtg"] + ramp * ratings["sos"]
    if prior_ratings is None:
        return current

    last_season = prior_ratings.set_index("team_id")
    strength = last_season["nrtg"] + last_season["sos"]
    prior = (config.PRIOR_REGRESSION * (strength - strength.mean())).reindex(ratings.index).fillna(0.0)
    weight = games / (games + config.PRIOR_BLEND_GAMES)
    return weight * current + (1 - weight) * prior


# ---------- star power (SPEC 6.1) ----------

def star_power(roster: pd.DataFrame, games_by_team: pd.Series) -> dict[str, StarResult]:
    """Return {team_id: StarResult}: 0.6 * BPM(best star) + 0.4 * BPM(second best).

    Stars must have played enough (at least 40% of the team's games, but between 3 and 20) and
    average 15+ minutes. If fewer than two players qualify, the minutes rule is relaxed to fill
    the slot (with a note). With one star the value is his BPM alone; with none it is None.
    """
    results = {}
    for team_id, g in games_by_team.items():
        team = roster[roster["team_id"] == team_id]
        needed = min(g, config.STAR_MIN_GAMES_CAP, max(config.STAR_MIN_GAMES_FLOOR, config.STAR_MIN_GAMES_FRAC * g))
        played = team[(team["g"] >= needed) & team["bpm"].notna() & (team["g"] > 0)]
        eligible = played[played["mp"] / played["g"] >= config.STAR_MIN_MPG]

        notes: list[str] = []
        chosen = _best_by_bpm(eligible, 2)
        if len(chosen) < 2:
            fill = _best_by_bpm(played.drop(chosen.index), 2 - len(chosen))
            if len(fill):
                notes.append(f"Star pick relaxed the {config.STAR_MIN_MPG} minutes-per-game rule")
                chosen = _best_by_bpm(pd.concat([chosen, fill]), 2)

        stars = tuple(Star(r.player_id, r.player, float(r.bpm)) for r in chosen.itertuples())
        if not stars:
            results[team_id] = StarResult(None, (), ("No player qualifies as a star yet",))
            continue
        if len(stars) == 1:
            notes.append("Only one qualifying star")
            results[team_id] = StarResult(stars[0].bpm, stars, tuple(notes))
            continue
        value = config.STAR_WEIGHTS[0] * stars[0].bpm + config.STAR_WEIGHTS[1] * stars[1].bpm
        results[team_id] = StarResult(value, stars, tuple(notes))
    return results


def _best_by_bpm(players: pd.DataFrame, count: int) -> pd.DataFrame:
    """Return the `count` players with the highest BPM (ties: more minutes first)."""
    return players.sort_values(["bpm", "mp"], ascending=False).head(count)


# ---------- star availability (SPEC 6.1) ----------

def star_availability(
    stars: dict[str, StarResult],
    seasons: list[pd.Series],
    games_by_team: pd.Series,
    injuries: pd.DataFrame,
    today: date,
) -> dict[str, AvailabilityResult]:
    """Return {team_id: AvailabilityResult}: how reliably each team's two stars play.

    `seasons` holds season_games() for this season, last season, and the season before (in that
    order). Per star: games played / games possible in each season, weighted 0.5 / 0.3 / 0.2.
    Seasons before the player's first appearance are skipped (rookies); a season he missed after
    that counts as 0. Then, only if INJURY_TIMELINES_ENABLED is False, the current injury report scales the
    result (INJURY_FACTORS).
    A team's value is the average over its stars.
    """
    results = {}
    for team_id, result in stars.items():
        if not result.stars:
            results[team_id] = AvailabilityResult(None)
            continue
        values, notes = [], []
        for star in result.stars:
            base = _player_availability(star.player_id, seasons, games_by_team[team_id])
            # With INJURY_TIMELINES_ENABLED the injury report is handled per player in pipeline/health.py
            # (SPEC 6.7); this input then keeps only the games-played history.
            factor, note = (1.0, None) if config.INJURY_TIMELINES_ENABLED else _injury_factor(star, injuries, today)
            values.append(base * factor)
            if note:
                notes.append(note)
        results[team_id] = AvailabilityResult(sum(values) / len(values), tuple(notes))
    return results


def _player_availability(player_id: str, seasons: list[pd.Series], team_games_now: float) -> float:
    """Return one player's weighted share of games played across the seasons (0 to 1)."""
    appears = [i for i, played in enumerate(seasons) if player_id in played.index]
    oldest = max(appears) if appears else 0  # seasons older than this one are before his first game
    weighted, total = 0.0, 0.0
    for i in range(oldest + 1):
        possible = team_games_now if i == 0 else config.SEASON_GAMES
        share = min(1.0, seasons[i].get(player_id, 0) / possible) if possible > 0 else 0.0
        weight = config.AVAILABILITY_SEASON_WEIGHTS[i]
        weighted += weight * share
        total += weight
    return weighted / total


def _injury_factor(star: Star, injuries: pd.DataFrame, today: date) -> tuple[float, str | None]:
    """Return (multiplier, note) from the star's latest injury entry; (1.0, None) if healthy.

    "Day To Day" entries older than DAY_TO_DAY_MAX_AGE_DAYS are treated as stale and ignored.
    """
    mine = injuries[injuries["player_id"] == star.player_id]
    if mine.empty:
        return 1.0, None
    latest = mine.sort_values("update").iloc[-1]
    factor = config.INJURY_FACTORS.get(latest["status"])
    if factor is None:
        return 1.0, None
    age_days = (pd.Timestamp(today) - latest["update"]).days
    if latest["status"] == "Day To Day" and age_days > config.DAY_TO_DAY_MAX_AGE_DAYS:
        return 1.0, None
    return factor, f"{star.name} (star): {latest['status']}"


# ---------- rotation availability (SPEC 6.1) ----------

def rotation_availability(roster: pd.DataFrame, games_by_team: pd.Series) -> dict[str, float | None]:
    """Return {team_id: value}: of the top 9 players by minutes, the minutes-weighted share of the
    team's games each has played. None for a team with no players."""
    results = {}
    for team_id, g in games_by_team.items():
        top = roster[roster["team_id"] == team_id].sort_values("mp", ascending=False).head(config.ROTATION_SIZE)
        if top.empty or g <= 0 or top["mp"].sum() <= 0:
            results[team_id] = None
            continue
        share = (top["g"] / g).clip(upper=1.0)
        results[team_id] = float((top["mp"] * share).sum() / top["mp"].sum())
    return results


# ---------- recent form (SPEC 6.1) ----------

def recent_form(games: pd.DataFrame, team_ids: list[str]) -> dict[str, float | None]:
    """Return {team_id: value}: average points scored minus allowed over the last 20 finished
    games. None if a team has fewer than FORM_MIN_GAMES finished games.

    `games` is parse_games() output (finished games only). Point difference per game is used,
    not per 100 possessions, because the results pages have scores only.
    """
    ordered = games.reset_index(drop=True).assign(order=lambda df: range(len(df)))
    home = pd.DataFrame({"team": ordered["home_id"], "diff": ordered["home_pts"] - ordered["away_pts"],
                         "date": ordered["date"], "order": ordered["order"]})
    away = pd.DataFrame({"team": ordered["away_id"], "diff": ordered["away_pts"] - ordered["home_pts"],
                         "date": ordered["date"], "order": ordered["order"]})
    long = pd.concat([home, away]).sort_values(["date", "order"], kind="stable")

    results: dict[str, float | None] = {}
    for team_id in team_ids:
        last = long[long["team"] == team_id].tail(config.FORM_GAMES)
        results[team_id] = float(last["diff"].mean()) if len(last) >= config.FORM_MIN_GAMES else None
    return results
