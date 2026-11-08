"""A fake 30-team league, rendered as Basketball-Reference-shaped pages, for end-to-end tests.

Team i (0 to 29) has strength s = i - 14.5, so the order of the teams is known in advance. Nothing
here is real data, and a FakeClient serves the pages so no network is used.
"""
from datetime import date, timedelta

from pipeline import config

TEAMS = config.TEAMS
STRENGTH = {t.id: i - 14.5 for i, t in enumerate(TEAMS)}
PLAYERS_PER_TEAM = 12


def table(table_id: str, headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<html><body><table id="{table_id}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>'


def ratings_page(games: int) -> str:
    rows = []
    for t in TEAMS:
        s = STRENGTH[t.id]
        wins = max(0, min(games, round(games / 2 + s / 6)))
        rows.append([t.name, str(wins), str(games - wins), f"{s / 2:.2f}", "0.00", f"{s / 2:.2f}",
                     f"{113 + s / 3:.1f}", f"{113 - s / 3:.1f}", f"{2 * s / 3:.1f}"])
    rows.append(["League Average", "", "", "0.00", "0.00", "0.00", "113.0", "113.0", ""])
    return table("advanced-team", ["Team", "W", "L", "MOV", "SOS", "SRS", "ORtg", "DRtg", "NRtg"], rows)


def player_id(team, j: int) -> str:
    return f"{team.br_code.lower()}p{j:02d}01"


def players_page(games: int, current: bool, players_per_team: int = PLAYERS_PER_TEAM) -> str:
    rows = []
    for t in TEAMS:
        for j in range(players_per_team):
            g = games - (j % 3) if current else 82 - (j % 3) * 3
            minutes = 30 * g - j * 60 if current else 2500 - j * 200
            bpm = STRENGTH[t.id] / 3 - 0.8 * j
            pid = player_id(t, j)
            rows.append([f'<a href="/players/{pid[0]}/{pid}.html">{t.id} Player {j}</a>', t.br_code,
                         str(g), str(minutes), f"{bpm:.1f}"])
    rows.append(["League Average", "", "", "", "0.0"])
    return table("advanced", ["Player", "Team", "G", "MP", "BPM"], rows)


def injuries_page() -> str:
    first, second = TEAMS[29], TEAMS[10]
    rows = [
        [f'<a href="/players/x/{player_id(first, 0)}.html">{first.id} Player 0</a>', first.name,
         "Sat, Nov 7, 2026", "Out (Knee) - will be re-evaluated in six weeks."],
        [f'<a href="/players/x/{player_id(second, 1)}.html">{second.id} Player 1</a>', second.name,
         "Fri, Nov 6, 2026", "Day To Day (Ankle) - sore."],
    ]
    return table("injuries", ["Player", "Team", "Update", "Description"], rows)


def games_page(start: date, days: int, skip_team: str | None = None, extra_rows: list | None = None) -> str:
    """`days` days of games starting at `start`: every team plays once a day (circle method).

    `extra_rows` (already-built table rows, e.g. unplayed games) are appended to the page.
    """
    ids = [t.id for t in TEAMS]
    rows = []
    for d in range(days):
        rotated = ids[d % 30:] + ids[:d % 30]
        day = start + timedelta(days=d)
        for k in range(15):
            home, away = rotated[k], rotated[29 - k]
            if skip_team in (home, away):
                continue
            margin = round((STRENGTH[home] - STRENGTH[away]) / 4) + 3
            rows.append([f"{day:%a, %b} {day.day}, {day.year}", config.TEAM_BY_ID[away].name, str(100),
                         config.TEAM_BY_ID[home].name, str(100 + margin), ""])
    rows += extra_rows or []
    return table("schedule", ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1", "Notes"], rows)


MONTH_NAMES = {10: "october", 11: "november", 12: "december", 1: "january", 2: "february", 3: "march", 4: "april"}


def future_rows_by_month(games: int, end_year: int) -> dict[str, list[list[str]]]:
    """Unplayed games (no score) for the rest of the season, as table rows grouped by month name.

    Each team has 82 - `games` games left: one "day" per remaining game (everyone plays once), spread
    from the day after the last played game to April 10 of `end_year`.
    """
    last = date(end_year - 1, 10, 20) + timedelta(days=games - 1) if games <= 12 else date(end_year - 1, 11, 1) + timedelta(days=games - 13)
    first, end = last + timedelta(days=1), date(end_year, 4, 10)
    slots = 82 - games
    ids = [t.id for t in TEAMS]
    by_month: dict[str, list[list[str]]] = {}
    for k in range(slots):
        day = first + timedelta(days=(k * (end - first).days) // max(slots - 1, 1))
        rotated = ids[k % 30:] + ids[:k % 30]
        for j in range(15):
            home, away = rotated[j], rotated[29 - j]
            by_month.setdefault(MONTH_NAMES[day.month], []).append(
                [f"{day:%a, %b} {day.day}, {day.year}", config.TEAM_BY_ID[away].name, "", config.TEAM_BY_ID[home].name, "", ""])
    return by_month


def league_pages(games: int = 24, skip_form_team: str | None = None, players_per_team: int = PLAYERS_PER_TEAM,
                 end_year: int = 2027) -> dict[str, str]:
    """Every page a build asks for, keyed by path. `games` is how many each team has played.

    Includes the monthly results pages for the rest of the season (unplayed games, no scores), which the
    shadow-mode simulation reads; the current month's page holds both played and unplayed games.
    """
    future = future_rows_by_month(games, end_year)
    pages = {
        f"/leagues/NBA_{end_year}.html": ratings_page(games),
        f"/leagues/NBA_{end_year - 1}.html": ratings_page(82),
        f"/leagues/NBA_{end_year}_advanced.html": players_page(games, True, players_per_team),
        f"/leagues/NBA_{end_year - 1}_advanced.html": players_page(82, False),
        f"/leagues/NBA_{end_year - 2}_advanced.html": players_page(82, False),
        "/friv/injuries.fcgi": injuries_page(),
        f"/leagues/NBA_{end_year}_games-october.html": games_page(date(2026, 10, 20), min(games, 12), skip_form_team, future.pop("october", None)),
        f"/leagues/NBA_{end_year}_games-november.html": games_page(date(2026, 11, 1), max(0, games - 12) or 1, skip_form_team, future.pop("november", None)),
    }
    for month, rows in future.items():
        pages[f"/leagues/NBA_{end_year}_games-{month}.html"] = table(
            "schedule", ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1", "Notes"], rows)
    return pages


class FakeClient:
    """Stands in for BRClient: serves pages from a dict and records what was asked for.

    Like the real client, a page asked for twice (same cache name) is served from the cache and
    counts as one request.
    """

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.paths: list[str] = []
        self.requests_made = 0
        self._seen: set[str] = set()

    def get_html(self, path: str, cache_name: str) -> str:
        if cache_name not in self._seen:
            self._seen.add(cache_name)
            self.paths.append(path)
            self.requests_made += 1
        return self.pages[path]


# ---------- a fake league for the playoff simulation (pipeline/sim.py) ----------

def sim_league(rating=None, games: int = 82, wins=None, remaining=None):
    """A 30-team League for sim tests. Team i has rating 0.4 * (i - 14.5) points unless `rating`
    (a list of 30 numbers) is given, and wins 41 + 2 * (i - 14.5), so wins are distinct inside each
    conference. `remaining` is a list of (home_index, away_index) games still to play."""
    import numpy as np
    from pipeline.sim import League
    strength = np.array([STRENGTH[t.id] for t in TEAMS])
    rate = 0.4 * strength if rating is None else np.asarray(rating, dtype=float)
    won = np.round(41 + 2 * strength) if wins is None else np.asarray(wins, dtype=float)
    conference = np.array([0 if t.conference == "East" else 1 for t in TEAMS])
    home = np.array([h for h, _ in remaining or []], dtype=int)
    away = np.array([a for _, a in remaining or []], dtype=int)
    return League([t.id for t in TEAMS], rate, np.full(30, games, dtype=float), won, conference, home, away)


def full_schedule(seed: int = 0) -> list[tuple[int, int]]:
    """About a full 82-game season of (home, away) pairs: every ordered pair once, plus 360 random extras."""
    import numpy as np
    rng = np.random.default_rng(seed)
    games = [(h, a) for h in range(30) for a in range(30) if h != a]
    while len(games) < 1230:
        h, a = rng.choice(30, 2, replace=False)
        games.append((int(h), int(a)))
    return games
