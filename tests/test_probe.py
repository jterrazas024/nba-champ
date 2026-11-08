"""Offline tests for the Phase 3 probe helpers. No network: they use small fake pages."""
import pandas as pd

from pipeline.spike.probe_sources import looks_like_bot_check, missing_columns, parse_tables

PLAIN_PAGE = """
<table><thead><tr><th>Player</th><th>Team</th><th>G</th></tr></thead>
<tbody><tr><td>A. Player</td><td>BOS</td><td>12</td></tr></tbody></table>
"""

# Basketball-Reference hides some tables inside HTML comments.
COMMENTED_PAGE = "<div><!-- " + PLAIN_PAGE + " --></div>"

TWO_ROW_HEADER_PAGE = """
<table><thead>
<tr><th></th><th colspan="2">Per Game</th></tr>
<tr><th>Team</th><th>MP</th><th>BPM</th></tr></thead>
<tbody><tr><td>BOS</td><td>34</td><td>5.1</td></tr></tbody></table>
"""


def test_parses_a_plain_table():
    tables = parse_tables(PLAIN_PAGE)
    assert len(tables) == 1
    assert list(tables[0].columns) == ["Player", "Team", "G"]


def test_finds_a_table_hidden_in_a_comment():
    assert len(parse_tables(COMMENTED_PAGE)) == 1


def test_two_row_headers_are_flattened_to_the_bottom_row():
    tables = parse_tables(TWO_ROW_HEADER_PAGE)
    assert list(tables[0].columns) == ["Team", "MP", "BPM"]


def test_page_without_tables_returns_empty_list():
    assert parse_tables("<html><body>nothing here</body></html>") == []


def test_missing_columns_reports_only_what_is_absent():
    tables = [pd.DataFrame(columns=["Player", "Team", "G"])]
    assert missing_columns(tables, ("Player", "G", "BPM")) == ("BPM",)


def test_either_name_satisfies_an_alternative():
    tables = [pd.DataFrame(columns=["Player", "Tm"])]
    assert missing_columns(tables, ("Player", "Team|Tm")) == ()


def test_detects_a_bot_check_page():
    assert looks_like_bot_check("<title>Just a moment...</title>")
    assert not looks_like_bot_check(PLAIN_PAGE)
