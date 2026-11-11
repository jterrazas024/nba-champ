"""Injury and return timelines: who is healthy for the playoffs, and what it is worth (SPEC.md section 6.7).

Pure functions on already-parsed tables; no network and no files. Everything here is switched on and off
by config.INJURY_TIMELINES_ENABLED (checked by the callers, features.star_availability and the build).

The idea. A title is won in the playoffs, so for each injured rotation player we ask how much of him
will be there then, and compare that with how much of him the team's current ratings already reflect:

    adjustment = value x (playoff_credit - reflected)         (in composite z units)

  value        (BPM - replacement) x minutes share, in points of margin, divided by MARGIN_PER_Z
               so it is on the same scale as the composite (the simulation turns z into points with it).
  credit       0 to 1 from the expected return date (playoff_credit): back well before the playoffs is
               about 1, out for the season is 0, back near the playoffs is partial.
  reflected    the share of him already inside the team's ratings: his share of games played this season
               times the current-season weight w = g / (g + 20), plus PRIOR_ASSUMED_HEALTH times (1 - w) for
               the last-season prior. This stops us double counting a star who has been out since October
               (his absence is already in the net rating) and means a long-injured star's return LIFTS a team.

Players are the injury report's players who are rotation-level (HEALTH_MIN_MPG or more) with a BPM above
replacement. Players with no usable stats (rookies, no minutes) are skipped.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from pipeline import config


@dataclass(frozen=True)
class PlayerHealth:
    """One injured player's effect on his team (adjustment is in composite z units; negative hurts)."""

    player_id: str
    name: str
    status: str
    expected_return: date | None
    category: str
    credit: float
    value_z: float
    reflected: float
    adjustment: float


@dataclass(frozen=True)
class TeamHealth:
    """A team's total health adjustment (capped; composite z units), the players behind it, and notes."""

    adjustment: float = 0.0
    players: tuple[PlayerHealth, ...] = ()
    notes: tuple[str, ...] = ()


def playoff_start(end_year: int) -> date:
    """Return the assumed first day of the playoffs for the season ending in `end_year`."""
    month, day = config.PLAYOFF_START
    return date(end_year, month, day)


def regular_season_end(end_year: int) -> date:
    """Return the assumed last day of the regular season."""
    month, day = config.REGULAR_SEASON_END
    return date(end_year, month, day)


def _credit_for(back: date, start: date) -> float:
    """Return the credit (0 to 1) for being back on `back` when the playoffs start on `start`.

    Back at least FULL_CREDIT_DAYS_BEFORE days before: 1. From then to day one: falls linearly to
    CREDIT_AT_PLAYOFF_START. After day one: falls linearly to 0 over CREDIT_FADE_DAYS_AFTER days.
    """
    before = (start - back).days
    if before >= config.FULL_CREDIT_DAYS_BEFORE:
        return 1.0
    if before >= 0:
        span = config.FULL_CREDIT_DAYS_BEFORE
        return config.CREDIT_AT_PLAYOFF_START + (1 - config.CREDIT_AT_PLAYOFF_START) * before / span
    return max(0.0, config.CREDIT_AT_PLAYOFF_START * (1 + before / config.CREDIT_FADE_DAYS_AFTER))


def playoff_credit(expected_return: date | None, category: str, today: date, end_year: int) -> float:
    """Return how much of a player is available for the playoffs (0 to 1).

    "season" (or any category with no return date): 0. Otherwise the credit for the return date,
    averaged over RETURN_DELAY_SCENARIOS (timelines slip more than they speed up), minus a small
    SEEDING_COST times the share of the remaining regular season he misses. A return date already in
    the past counts as healthy for short absences ("single_game", "day_to_day"); for a timed injury that
    is still listed it means "overdue", so INJURY_OVERDUE_DAYS more days are assumed. The result is capped at
    HEALTH_CREDIT_CEILING (a recovered player is only as available as the league's average healthy player, who
    also misses games) and discounted for categories whose return date is a guess (HEALTH_UNCERTAIN_CREDIT). Once the playoffs have
    started, the credit is measured from today instead of the assumed first day.
    """
    if category == "season" or expected_return is None:
        return 0.0
    credit = _raw_credit(expected_return, category, today, end_year)
    credit *= config.HEALTH_UNCERTAIN_CREDIT.get(category, 1.0)
    return min(credit, config.HEALTH_CREDIT_CEILING)


def _raw_credit(expected_return: date, category: str, today: date, end_year: int) -> float:
    """Return the playoff credit before the ceiling and the uncertainty discount (see playoff_credit)."""
    start = max(playoff_start(end_year), today)
    back = expected_return
    if back <= today:
        if category in ("single_game", "day_to_day"):
            return 1.0
        back = today + timedelta(days=config.INJURY_OVERDUE_DAYS)
    credit = sum(weight * _credit_for(back + timedelta(days=delay), start)
                 for delay, weight in config.RETURN_DELAY_SCENARIOS)
    remaining = max(1, (regular_season_end(end_year) - today).days)
    missed = min(1.0, max(0.0, (back - today).days / remaining))
    return max(0.0, credit - config.SEEDING_COST * missed)


def player_value_points(player_id: str, roster: pd.DataFrame, prior_roster: pd.DataFrame | None) -> float | None:
    """Return a player's value in points of margin per game (BPM above replacement x minutes share), or None.

    Uses this season's BPM and minutes once he has HEALTH_MIN_CURRENT_GAMES games; otherwise last
    season's (needs HEALTH_MIN_PRIOR_GAMES games). BPM is shrunk toward 0 by HEALTH_BPM_SHRINK. A player
    below HEALTH_MIN_MPG, or at or below replacement level, is worth 0.
    """
    source = None
    for table, min_games in ((roster, config.HEALTH_MIN_CURRENT_GAMES), (prior_roster, config.HEALTH_MIN_PRIOR_GAMES)):
        if table is None or player_id not in table.index:
            continue
        row = table.loc[player_id]
        if row["g"] >= min_games and pd.notna(row["bpm"]) and row["mp"] > 0:
            source = row
            break
    if source is None:
        return None
    mpg = source["mp"] / source["g"]
    if mpg < config.HEALTH_MIN_MPG:
        return 0.0
    above = config.HEALTH_BPM_SHRINK * source["bpm"] - config.REPLACEMENT_BPM
    return max(0.0, above) * min(mpg, config.HEALTH_MAX_MPG) / 48.0


def _reflected(player_id: str, roster: pd.DataFrame, team_games: float) -> float:
    """Return the share of a player already reflected in the team's current ratings (0 to 1).

    w x (his share of this season's games played) + (1 - w) x PRIOR_ASSUMED_HEALTH, with
    w = games / (games + PRIOR_BLEND_GAMES) as in net_rating(). The constant is below 1 because every
    team's last-season rating already includes ordinary absences; using each player's own last-season
    games instead made returns of injury-prone stars look too valuable.
    """
    w = team_games / (team_games + config.PRIOR_BLEND_GAMES)
    played = float(roster.loc[player_id, "g"]) if player_id in roster.index else 0.0
    present = min(1.0, played / team_games) if team_games > 0 else 1.0
    return w * present + (1 - w) * config.PRIOR_ASSUMED_HEALTH


def _note(name: str, status: str, expected: date | None, category: str, adjustment: float) -> str:
    """Return a short note such as "Jimmy Butler out, back ~Jan 30: +0.12"."""
    if category == "season":
        when = "out for the season"
    elif expected is None:
        when = status.lower()
    else:
        when = f"{'back' if status != 'Day To Day' else 'day to day, back'} ~{expected:%b} {expected.day}"
    return f"{name}: {when} ({adjustment:+.2f})"


def team_health(
    injuries: pd.DataFrame,
    roster: pd.DataFrame,
    prior_roster: pd.DataFrame | None,
    games_by_team: pd.Series,
    today: date,
    end_year: int,
) -> dict[str, TeamHealth]:
    """Return {team_id: TeamHealth} for every team in `games_by_team`.

    `injuries` is parse_injuries() output (latest entry per player counts; a "Day To Day" entry older
    than DAY_TO_DAY_MAX_AGE_DAYS is stale and ignored). `roster` and `prior_roster` are
    features.current_roster() for this and last season. Each team's adjustment is the sum over its injured
    players, clamped to [-HEALTH_ADJ_MAX_DOWN, +HEALTH_ADJ_MAX_UP].

    Where this meets the 15-point daily guard (build_odds.MAX_DAILY_MOVE): the adjustment is part of the
    composite, so it moves title_prob like any other input and is subject to the same guard. The cap keeps a
    single injury from moving a team by more than about 0.8 z (a factor of about 3.8 in softmax weight);
    flipping the switch can still trip the guard for one team, which means the owner should review that day.
    """
    result: dict[str, TeamHealth] = {tid: TeamHealth() for tid in games_by_team.index}
    if injuries.empty:
        return result
    by_player = roster.set_index("player_id")
    prior = None if prior_roster is None else prior_roster.set_index("player_id")
    latest = injuries.sort_values("update").groupby("player_id", sort=False).tail(1)

    entries: dict[str, list[PlayerHealth]] = {tid: [] for tid in result}
    for row in latest.itertuples():
        if row.team_id not in entries or not isinstance(row.player_id, str):
            continue
        category = getattr(row, "return_category", "unknown")
        expected = getattr(row, "expected_return", None)
        expected = None if expected is None or (not isinstance(expected, date) and pd.isna(expected)) else expected
        age = (pd.Timestamp(today) - row.update).days if pd.notna(row.update) else 0
        if row.status == "Day To Day" and age > config.DAY_TO_DAY_MAX_AGE_DAYS:
            continue
        if row.status not in ("Out", "Out For Season", "Day To Day"):
            continue
        points = player_value_points(row.player_id, by_player, prior)
        if not points:
            continue
        value_z = points / config.MARGIN_PER_Z
        credit = playoff_credit(expected, category, today, end_year)
        reflected = _reflected(row.player_id, by_player, float(games_by_team[row.team_id]))
        adjustment = value_z * (credit - reflected)
        if category != "season" and expected is not None and (expected - today).days < config.HEALTH_SHORT_ABSENCE_DAYS:
            adjustment = 0.0   # a short absence is noise, not a health story (the games already missed are in the ratings)
        entries[row.team_id].append(PlayerHealth(row.player_id, row.player, row.status, expected, category,
                                                 round(credit, 3), round(float(value_z), 3), round(reflected, 3),
                                                 round(float(adjustment), 4)))
    for tid, players in entries.items():
        if not players:
            continue
        total = min(config.HEALTH_ADJ_MAX_UP, max(-config.HEALTH_ADJ_MAX_DOWN, sum(p.adjustment for p in players)))
        ranked = sorted(players, key=lambda p: -abs(p.adjustment))
        notes = tuple(_note(p.name, p.status, p.expected_return, p.category, p.adjustment)
                      for p in ranked if abs(p.adjustment) >= config.HEALTH_NOTE_MIN)
        result[tid] = TeamHealth(round(float(total), 4), tuple(ranked), notes)
    return result
