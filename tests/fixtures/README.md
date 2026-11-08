# Test fixtures

Small slices of real Basketball-Reference pages, fetched once on 2026-11-07 and trimmed to a few
rows each (table header plus selected rows, with the real HTML markup and links kept). They let
the parser tests run offline and fast.

| File | Table id | What is in it |
|---|---|---|
| `ratings_sample.html` | `advanced-team` | 8 teams plus the "League Average" row |
| `players_sample.html` | `advanced` | last season's page: a high-minutes player, a traded player (`2TM` total row plus one row per team), an accented name, a 3-game sample, and "League Average" |
| `injuries_sample.html` | `injuries` | two entries for each status (Out, Day To Day, Out For Season) |
| `games_sample.html` | `schedule` | 4 finished games (2 NBA Cup) and 3 future games with no score |

Real pages change layout from time to time. If the site changes, refresh the fixtures from a new
fetch (one polite request per page, through `pipeline/fetch.py`) and rerun the tests.
