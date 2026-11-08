"""Tests for the full-schedule reader (pipeline/schedule.py) and its thin parse_games wrapper."""
import glob
import re
from pathlib import Path

import pandas as pd
import pytest

from pipeline.backtest.history import team_maps
from pipeline.parse import parse_games
from pipeline.schedule import SCHEDULE_COLUMNS, parse_schedule

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BACKTEST = Path(__file__).resolve().parent.parent / "data" / "raw" / "backtest"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_columns_and_separator_row_is_not_a_game():
    games = parse_schedule(fixture("schedule_separator_sample.html"))
    assert list(games.columns) == SCHEDULE_COLUMNS
    assert len(games) == 4 and games["date"].notna().all()


def test_old_layout_marks_games_after_the_playoffs_row_as_playoffs():
    games = parse_schedule(fixture("schedule_separator_sample.html"))
    assert games["stage"].tolist() == ["regular", "regular", "playoffs", "playoffs"]


def test_playin_era_layout_has_no_separator_and_is_split_by_date():
    games = parse_schedule(fixture("schedule_playin_era_sample.html"))
    assert games["stage"].tolist() == ["regular", "regular", "playin", "playin", "playoffs", "playoffs"]
    assert games.loc[games["stage"] == "playin", "notes"].str.contains("Play-In").all()


def test_unplayed_games_are_kept_with_nan_scores():
    games = parse_schedule(fixture("schedule_current_sample.html"))
    assert games["is_played"].tolist() == [True, True, False, False]
    assert games.loc[~games["is_played"], ["away_pts", "home_pts"]].isna().all().all()
    assert set(games["stage"]) == {"regular"}


def test_playoffs_from_marks_a_page_with_no_marker():
    games = parse_schedule(fixture("schedule_current_sample.html"), playoffs_from="2026-11-07")
    assert games["stage"].tolist() == ["regular", "regular", "playoffs", "playoffs"]


def test_parse_games_is_the_played_subset_in_the_old_shape():
    wrapped = parse_games(fixture("schedule_current_sample.html"))
    assert list(wrapped.columns) == ["date", "away_id", "away_pts", "home_id", "home_pts", "notes"]
    assert len(wrapped) == 2 and wrapped["away_pts"].dtype.kind == "i"


def test_parse_games_still_includes_finished_playoff_games():
    """Documented behavior: parse_games does not filter by stage (the backtest cuts pages itself)."""
    assert len(parse_games(fixture("schedule_playin_era_sample.html"))) == 6


def test_unknown_team_gets_no_id_and_parse_games_drops_it():
    page = fixture("schedule_current_sample.html").replace("Los Angeles Lakers", "Seattle SuperSonics")
    assert parse_schedule(page)["home_id"].isna().sum() == 1
    assert len(parse_games(page)) == 1


@pytest.mark.skipif(not list(BACKTEST.glob("games_*_april.html")), reason="backtest cache not present")
@pytest.mark.parametrize("path", sorted(glob.glob(str(BACKTEST / "games_*_april.html"))))
def test_every_cached_april_page_parses(path):
    year = int(re.search(r"games_(\d+)_april", path).group(1))
    games = parse_schedule(Path(path).read_text(encoding="utf-8", errors="replace"), team_maps(year)[0])
    assert len(games) > 100 and games["home_id"].notna().all() and games["is_played"].all()
    if (games["stage"] == "playoffs").any():                              # the 2021 April page ends before the play-in
        assert games.loc[games["stage"] == "regular", "date"].max() <= games.loc[games["stage"] == "playoffs", "date"].min()
    assert (games["stage"] == "playin").sum() == (6 if year >= 2022 else 0)


def test_unplayed_regular_games_drops_played_games_and_the_cup_final():
    from pipeline.sim_inputs import unplayed_regular_games
    from tests.synthetic import table
    head = ["Date", "Visitor/Neutral", "PTS", "Home/Neutral", "PTS.1", "Notes"]
    page = table("schedule", head, [
        ["Mon, Nov 2, 2026", "Boston Celtics", "100", "Atlanta Hawks", "99", ""],
        ["Tue, Nov 3, 2026", "Boston Celtics", "", "Atlanta Hawks", "", ""],
        ["Sat, Dec 12, 2026", "Boston Celtics", "", "Atlanta Hawks", "", "NBA Cup Final"],
        ["Sun, Dec 6, 2026", "Boston Celtics", "", "Atlanta Hawks", "", "NBA Cup Semifinals"],
    ])
    games = unplayed_regular_games([page])
    assert len(games) == 2 and set(games["home_id"]) == {"ATL"}


def _ids():
    from pipeline import config
    return [t.id for t in config.TEAMS]


def _full_schedule(left: int):
    """A schedule where every team has `left` games: `left` rounds of 15 games (everyone plays once a round)."""
    import pandas as pd
    ids = _ids()
    rows = []
    for k in range(left):
        rotated = ids[k % 30:] + ids[:k % 30]
        rows += [(rotated[j], rotated[29 - j]) for j in range(15)]
    return pd.DataFrame(rows, columns=["home_id", "away_id"])


def test_complete_remaining_fills_a_few_missing_games_and_is_deterministic():
    import numpy as np
    import pandas as pd
    from pipeline.sim_inputs import complete_remaining
    played = pd.Series(70, index=_ids())                               # 12 games left each
    short = _full_schedule(12).iloc[5:]                                # 5 games missing from the pages
    a = complete_remaining(short, played, np.random.default_rng(1))
    b = complete_remaining(short, played, np.random.default_rng(1))
    assert a.equals(b)
    per_team = pd.concat([a["home_id"], a["away_id"]]).value_counts().reindex(_ids())
    assert (per_team == 12).all()


def test_complete_remaining_rejects_big_gaps_and_double_counted_pages():
    import numpy as np
    import pandas as pd
    import pytest
    from pipeline.sim_inputs import complete_remaining
    played = pd.Series(70, index=_ids())
    with pytest.raises(ValueError, match="remaining schedule"):
        complete_remaining(_full_schedule(7), played, np.random.default_rng(1))      # a month missing: 5 games short
    with pytest.raises(ValueError, match="remaining schedule"):
        complete_remaining(_full_schedule(14), played, np.random.default_rng(1))     # a page counted twice: 2 over
    ok = complete_remaining(_full_schedule(13), played, np.random.default_rng(1))    # one over (the Cup final): left alone
    assert len(ok) == len(_full_schedule(13))
