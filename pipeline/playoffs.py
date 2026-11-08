"""Read the postseason from Basketball-Reference results pages (SPEC.md section 6.6).

Once every team has played 82 games the daily build switches to playoff mode. This module turns the
parsed schedule (April, May and June pages, see `pipeline.schedule`) into the inputs the simulator
needs: a `sim.PlayoffState` (bracket seeds, play-in results, the score of every series started)
and, for the site, who is still alive, who went out where, and the champion once there is one.

Layout facts (checked on the cached 2022-23, 2023-24 and 2024-25 pages, see tests/fixtures/README.md):
  * April holds the end of the regular season, the play-in ("Play-In Game" in the notes) and the
    first playoff games, with no marker; `pipeline.schedule` tells them apart by date.
  * May and June pages hold ONLY playoff games but carry no marker at all, so the schedule parser
    would call them "regular". We therefore parse May and June with `playoffs_from` set to the first
    day of the month.
  * The ratings page keeps showing 82 games per team during the playoffs (playoff games are not in it).

What this module cannot know and how it copes:
  * The real seeding tie-breaks. Teams are ordered by wins, then margin of victory. Once first-round
    games exist, the actual pairings are read from the games and the seeds are corrected to fit them.
  * Which games are play-in games before they are listed: the 7th to 10th teams in the standings.

Anything that does not fit (a game between teams that cannot meet, a series that jumps ahead of its
feeders) raises ParseError: in playoff mode a wrong bracket is worse than a failed run.
"""
import itertools
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from pipeline import config
from pipeline.parse import ParseError
from pipeline.schedule import parse_schedule
from pipeline.sim import PlayoffState, R1_PAIRS

POSTSEASON_MONTHS = ("april", "may", "june")
MONTH_NUMBER = {"april": 4, "may": 5, "june": 6}
ROUND_LABELS = {1: "the first round", 2: "the second round", 3: "the conference finals", 4: "the Finals"}
EARLY = date(1900, 1, 1)         # a `playoffs_from` date that is before every game: "all games on this page are playoffs"
MISSED = "Missed the playoffs"
LOST_PLAYIN = "Lost in the play-in"


@dataclass
class Postseason:
    """What the schedule says about the postseason.

    phase: "playin" (no playoff game played yet), "playoffs", or "complete" (a champion exists).
    state: the simulator input. champion: team id or None.
    out: team id -> short text for where it went out ("Lost in the second round"); every team that
    cannot win the title is in here, every team still alive is not.
    """

    phase: str
    state: PlayoffState
    champion: str | None
    out: dict[str, str] = field(default_factory=dict)

    def alive(self, ids: list[str]) -> list[str]:
        """Team ids that can still win the title."""
        return [t for t in ids if t not in self.out]


# ---------- reading the pages ----------

def postseason_months(today: date) -> tuple[str, ...]:
    """Return the monthly pages that can hold postseason games on `today` (April, then May and June as they arrive)."""
    last = min(max(today.month, 4), 6)
    return tuple(m for m in POSTSEASON_MONTHS if MONTH_NUMBER[m] <= last)


def parse_postseason_pages(pages: dict[str, str]) -> pd.DataFrame:
    """Return every game on the given pages ({"april": html, ...}) with stages set correctly.

    May and June pages hold only playoff games and carry no marker, so every game on them is
    marked as playoffs (see the module docstring). Output columns are those of `parse_schedule`.
    """
    frames = [parse_schedule(html, playoffs_from=None if month == "april" else EARLY)
              for month, html in pages.items()]
    return pd.concat(frames, ignore_index=True).sort_values("date", kind="stable").reset_index(drop=True)


def fetch_postseason(client, end_year: int, today: date) -> pd.DataFrame:
    """Fetch the April (required) and May/June (when they exist) results pages and return all games.

    The cache names match `fetch_recent_games` and the regular-season schedule fetch, so shared
    pages cost no extra request. A May or June page that does not exist yet (HTTP 404 before the
    month's first game is on the calendar) counts as "no games"; a block still stops the run.
    """
    from pipeline.fetch import Blocked, FetchError     # imported here: keep module import light
    pages = {}
    for month in postseason_months(today):
        try:
            pages[month] = client.get_html(f"/leagues/NBA_{end_year}_games-{month}.html", f"games_{end_year}_{month}")
        except Blocked:
            raise
        except FetchError:
            if month == "april":
                raise
    return parse_postseason_pages(pages)


# ---------- the bracket ----------

def _conference_standings(ratings: pd.DataFrame) -> dict[int, list[str]]:
    """Team ids of each conference (0 = East, 1 = West) ordered by wins, then margin of victory, then id."""
    conf = {t.id: 0 if t.conference == "East" else 1 for t in config.TEAMS}
    df = ratings.assign(conf=ratings["team_id"].map(conf)).sort_values(
        ["w", "mov", "team_id"], ascending=[False, False, True], kind="stable")
    return {c: list(df.loc[df["conf"] == c, "team_id"]) for c in (0, 1)}


def _winner(row) -> str:
    return row.home_id if row.home_pts > row.away_pts else row.away_id


def _tally(games: pd.DataFrame) -> dict[frozenset, dict]:
    """Group finished games by the two teams: {pair: {"wins": Counter, "host": home team of the first game}}."""
    out: dict[frozenset, dict] = {}
    for row in games.itertuples():
        info = out.setdefault(frozenset((row.home_id, row.away_id)), {"wins": Counter(), "host": row.home_id})
        info["wins"][_winner(row)] += 1
    return out


def _read_playin(c: int, order: list[str], games: pd.DataFrame, index: dict[str, int], out: dict[str, str]):
    """Work out one conference's play-in. Returns (direct qualifiers 1-6, play-in teams 7-10, results {game: winner index}).

    The four play-in teams are the ones in the listed play-in games when four are listed, otherwise
    positions 7-10 of the standings. The first two finished games on different teams are game 1
    (7 hosts 8) and game 2 (9 hosts 10); the next one is game 3. Teams that lose out are added to `out`.
    """
    rank = {t: i for i, t in enumerate(order)}
    listed = games[(games["stage"] == "playin") & games["home_id"].isin(order) & games["away_id"].isin(order)]
    names = set(listed["home_id"]) | set(listed["away_id"])
    members = names if len(names) == 4 else set(order[6:10])
    if not names <= members:
        raise ParseError(f"conference {c}: the play-in games listed do not match the standings")
    top6 = [t for t in order if t not in members][:6]
    for t in order:
        if t not in members and t not in top6:
            out[t] = MISSED
    ranked = sorted(members, key=rank.get)
    played = [(r.home_id, r.away_id, _winner(r)) for r in listed[listed["is_played"]].itertuples()]
    if not played:
        return top6, ranked, {}

    first_two, third, seen = [], None, set()
    for game in played:
        if len(first_two) < 2 and not ({game[0], game[1]} & seen):
            first_two.append(game)
            seen |= {game[0], game[1]}
        elif third is None:
            third = game
        else:
            raise ParseError(f"conference {c} has more play-in games than the format allows")
    first_two.sort(key=lambda g: rank[g[0]])               # the better-placed host is seed 7
    if len(first_two) == 2:
        g1, g2 = first_two
    elif min(rank[first_two[0][0]], rank[first_two[0][1]]) <= rank[ranked[1]]:
        g1, g2 = first_two[0], None
    else:
        g1, g2 = None, first_two[0]
    teams = list(g1[:2]) if g1 else ranked[:2]
    teams += list(g2[:2]) if g2 else ranked[2:]
    results = {}
    if g1:
        results[1] = index[g1[2]]
        out_loser = g1[1] if g1[2] == g1[0] else g1[0]
    if g2:
        results[2] = index[g2[2]]
        out[g2[1] if g2[2] == g2[0] else g2[0]] = LOST_PLAYIN
    if third:
        if not (g1 and g2) or {third[0], third[1]} != {out_loser, g2[2]}:
            raise ParseError(f"conference {c}: the third play-in game is not between the first game's loser and the second game's winner")
        results[3] = index[third[2]]
        out[third[1] if third[2] == third[0] else third[0]] = LOST_PLAYIN
    return top6, teams, results


def _first_round_pairs(playoffs: pd.DataFrame, bracket: set[str]) -> dict[frozenset, str]:
    """Return {pair: home team of its first game} for the round-1 series seen so far among `bracket`.

    A team's first playoff opponent is its round-1 opponent (series between the same two teams are
    never repeated), and the home team of game 1 is the better seed.
    """
    first: dict[str, str] = {}
    host: dict[frozenset, str] = {}
    for row in playoffs.itertuples():
        if row.home_id in bracket and row.away_id in bracket:
            host.setdefault(frozenset((row.home_id, row.away_id)), row.home_id)
            first.setdefault(row.home_id, row.away_id)
            first.setdefault(row.away_id, row.home_id)
    return {frozenset((t, o)): host[frozenset((t, o))] for t, o in first.items() if first.get(o) == t}


def _fit_seeds(c: int, top6: list[str], s7: str, s8: str, observed: dict[frozenset, str]) -> list[str]:
    """Order the six direct qualifiers so the bracket matches the round-1 series already seen.

    Starts from the standings order; if every observed series (and its home team) already fits the
    1v8, 4v5, 3v6, 2v7 pattern that order is kept. Otherwise the closest order that fits is used
    (least total movement from the standings). Raises ParseError if no order fits.
    """
    best, best_cost = None, None
    for perm in itertools.permutations(range(6)):
        seeds = [top6[i] for i in perm] + [s7, s8]
        cost = sum(abs(pos - src) for pos, src in enumerate(perm))
        if best_cost is not None and cost >= best_cost:
            continue
        slots = {frozenset((seeds[i], seeds[j])): seeds[i] for i, j in R1_PAIRS}
        if all(slots.get(pair) == host for pair, host in observed.items()):
            best, best_cost = seeds, cost
            if cost == 0:
                break
    if best is None:
        raise ParseError(f"conference {c}: the first-round games do not fit any bracket from the standings")
    return best


def read_postseason(games: pd.DataFrame, ratings: pd.DataFrame, ids: list[str]) -> Postseason:
    """Build the simulator state from the schedule (`parse_schedule` rows) and the final standings.

    `ratings` is parse_ratings output (team_id, w, mov); `ids` is the team order the simulator will
    use. Raises ParseError if the games do not fit a playoff bracket.
    """
    index = {t: i for i, t in enumerate(ids)}
    standings = _conference_standings(ratings)
    out: dict[str, str] = {}
    seeds: dict[int, list[int]] = {}
    playin: dict[int, list[int]] = {}
    results: dict[tuple[int, int], int] = {}
    full: dict[int, list[str] | None] = {}
    playoffs = games[(games["stage"] == "playoffs") & games["is_played"]].sort_values("date", kind="stable")
    tally = _tally(playoffs)
    for c in (0, 1):
        top6, teams, res = _read_playin(c, standings[c], games, index, out)
        done = 3 in res
        if done:
            s7, s8 = ids[res[1]], ids[res[3]]
            full[c] = _fit_seeds(c, top6, s7, s8, _first_round_pairs(playoffs, set(top6) | {s7, s8}))
            seeds[c] = [index[t] for t in full[c]]
        else:
            full[c] = None
            seeds[c] = [index[t] for t in top6]
            playin[c] = [index[t] for t in teams]
            results.update({(c, g): w for g, w in res.items()})

    scores: dict[tuple[int, int, int], tuple[int, int]] = {}
    used: set[frozenset] = set()
    champs: list[str | None] = []
    for c in (0, 1):
        champs.append(_play_conference(c, full[c], tally, scores, used, out) if full[c] else None)
    champion = None
    if all(champs):
        champion = _resolve(4, 0, 0, champs[0], champs[1], tally, scores, used, out)
    stray = [tuple(sorted(p)) for p in tally if p not in used]
    if stray:
        raise ParseError(f"playoff games between {stray[0][0]} and {stray[0][1]} do not fit the bracket")

    phase = "complete" if champion else "playoffs" if len(playoffs) else "playin"
    return Postseason(phase, PlayoffState(seeds, scores, playin, results), champion, out)


def _resolve(rnd: int, c: int, slot: int, a, b, tally, scores, used, out) -> str | None:
    """Record the score of the series between a and b (None if either is not decided yet) and return its winner."""
    if a is None or b is None:
        return None
    pair = frozenset((a, b))
    info = tally.get(pair)
    if info is None:
        return None
    used.add(pair)
    wa, wb = info["wins"][a], info["wins"][b]
    scores[(rnd, c, slot)] = (wa, wb)
    if max(wa, wb) < 4:
        return None
    winner, loser = (a, b) if wa > wb else (b, a)
    out[loser] = f"Lost in {ROUND_LABELS[rnd]}"
    return winner


def _play_conference(c: int, seeds: list[str], tally, scores, used, out) -> str | None:
    """Walk one conference's bracket through rounds 1-3 using the series tally. Returns the conference champion or None."""
    r1 = [_resolve(1, c, slot, seeds[i], seeds[j], tally, scores, used, out) for slot, (i, j) in enumerate(R1_PAIRS)]
    r2 = [_resolve(2, c, 0, r1[0], r1[1], tally, scores, used, out),
          _resolve(2, c, 1, r1[2], r1[3], tally, scores, used, out)]
    return _resolve(3, c, 0, r2[0], r2[1], tally, scores, used, out)
