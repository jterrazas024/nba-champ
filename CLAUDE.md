# CLAUDE.md

Project rules for Claude Code. Read this first every session. Full scope and tech details are in `SPEC.md`; the research behind the model is in `docs/research-plan.md`.

## What this project is

A live website that shows each NBA team's probability of winning the 2026-27 championship (a Polymarket/Kalshi-style odds dashboard with Apple-style restraint). A Python pipeline computes the odds from public NBA stats and writes `data/odds.json`. A static HTML/CSS/JS site on GitHub Pages renders that file. A scheduled GitHub Action refreshes the data daily.

## Working rules

- Prefer small steps. Propose a plan before large changes and wait for approval.
- Do not use Plotly or Dash in v1. No charting libraries; bars and sparklines are plain CSS or inline SVG.

## Stack

- **Pipeline:** Python 3.11+, `pandas`, `requests`, `pytest`. Keep dependencies minimal and list them in `requirements.txt`.
- **Site:** plain `index.html`, `style.css`, `app.js`. No build step, no frameworks, no npm in v1.
- **Hosting:** GitHub Pages (public repo, served from the repo root on `main`).
- **Automation:** GitHub Actions, workflow at `.github/workflows/refresh.yml`.

## Commands

- Install: `pip install -r requirements.txt` (inside the virtual environment: `python3.12 -m venv .venv`, then use `.venv/bin/python` and `.venv/bin/pytest`)
- Build odds (run daily by the Action): `python pipeline/build_odds.py` (add `--out /some/path.json` to write somewhere else while experimenting)
- Check the plan without sending requests: `python pipeline/spike/probe_sources.py --dry-run`
- Run the refresh Action by hand: `gh workflow run refresh.yml`, then `gh run list --workflow refresh.yml`
- Run tests: `pytest`
- Replay a past postseason offline at chosen dates (cached pages, no network): `python pipeline/replay_playoffs.py 2025 2025-04-13 2025-05-10 --pages data/raw/playoff_replay data/raw/backtest`
- Calibrate/score the playoff simulation (offline, about 90 seconds, not part of the daily build): `python pipeline/calibrate_sim.py`
- Backtest (not part of the daily build): `python pipeline/backtest/fetch_history.py` (one-off, about 140 requests, cached in `data/raw/backtest/`), then `python pipeline/backtest/run_backtest.py`
- Preview site: `python3 -m http.server 8000` then open http://localhost:8000 (`?theme=amber|black` forces a theme; fonts in `fonts/`, Anton under the SIL OFL; team crests are original SVG monograms, not official logos)
  (opening `index.html` by double-click will not load `odds.json` because of browser rules, so use the local server)

## The contract: `data/odds.json`

The site reads only this file. The pipeline is the only thing that writes it. Do not change its shape without updating `SPEC.md` (section "odds.json schema"), the pipeline, the site, and the tests together. Required rules:

- Title probabilities across all 30 teams sum to 1.0 (tests must check this).
- Includes `generated_at` (UTC ISO timestamp) so the site can show "Last updated".
- File stays under 100 KB.

## Model rules

- After game 82 the build is in playoff mode (SPEC.md section 6.6): `title_prob` comes from the simulation conditioned on the play-in and series played; eliminated teams are exactly 0; the 15-point daily guard does not apply there (a no-revival check does). Read the postseason through `pipeline/playoffs.py`; never publish a guessed bracket.
- Starting weights live in one place (`pipeline/config.py`) and must sum to 1.0. In points: net rating 38, star power 25, recent form 10, star availability 10, rotation availability 5, divided by 88. Clutch and core net rating are dropped (weight 0; no usable data source).
- If an input is not available, renormalize the remaining weights rather than filling with fake numbers (per team if just that team lacks it).
- Early season: blend current-season stats with a preseason prior using `w_current = games_played / (games_played + 20)`.
- Injuries: `config.INJURY_TIMELINES_ENABLED` (SPEC.md 6.7) switches per-player return timelines and a capped team health adjustment; False restores the old top-2-star injury factor exactly.
- Star metric is BPM for now. Don't swap it without asking.
- Never present placeholder or fake data as real. Fake data is only for tests and must be labeled `"is_sample": true` if it ever lands in `odds.json`. The live file is real data built by `build_odds.py`.

## Data source rules

- Basketball-Reference (the chosen source; tested from Actions Nov 7, 2026): **never exceed 20 requests per minute** (a violation blocks the IP for up to a day). Pace requests (one every ~4 seconds; robots.txt asks for 3), cache every page under `data/raw/`, and never re-fetch what is already cached for the day. Identify the project in the User-Agent. Stay out of the paths robots.txt disallows (`*/gamelog/`, `*/splits/`, `*/on-off/`, `*/lineups/`, `*/shooting/`, `/play-index/` searches). **On any 403, 429, or bot-check page, stop the whole run and do not retry.** It can block non-US IPs (a Mexican IP got a Cloudflare 403), so test from a US location.
- `stats.nba.com` / `nba_api` and `cdn.nba.com`: **do not use.** They time out or return 403 from every network we tried (home, hotspot, VPN), and others report 403 from GitHub Actions. Never disguise requests as a browser or otherwise work around a block; ask first.
- Every fetch module must have timeouts, retries with backoff, and a clear error message on failure. If a refresh fails, the Action must fail loudly and leave the previous `odds.json` in place rather than publishing broken data.
- No API keys in the repo. If one is ever needed, it goes in GitHub Actions secrets and a local `.env` that is git-ignored.

## Git and workflow habits

- Work on a branch for each feature (`feature/short-name`), merge to `main` when it works. `main` is what gets deployed.
- One logical change per commit, with a clear message in plain English.
- Before committing: run the tests and, for site changes, look at the page in a browser.
- Do not commit `data/raw/`, `.env`, virtual environments, or `__pycache__`. Keep `.gitignore` current.
- Never force-push or rewrite history on `main`.
- Show the diff summary before committing, and ask before pushing the first time in a session.

## Code style

- Python: type hints on functions, short functions, docstrings that say what a function returns. Formulas get a comment pointing to the section of `SPEC.md` they implement.
- JavaScript: plain ES modules-free script, `const`/`let`, no dependencies. Handle the "odds.json failed to load" case with a visible message.
- Charts: hand-built SVG inside `app.js` (no libraries). One color per series, thin lines, hover/keyboard readouts, and an accessible table for each chart. Look: amber and black themes, Anton display numbers, mono data rows, original SVG team crests.
- CSS: system font stack (`-apple-system, BlinkMacSystemFont, "SF Pro Text", ...`), CSS variables for colors, light and dark via `prefers-color-scheme`.
- Prefer clarity over cleverness.

## Definition of done for any task

1. It runs (pipeline and/or page) and the result has been checked.
2. Tests pass, and new logic has a test.
3. `SPEC.md` and this file are updated if behavior or commands changed.
4. Changes are committed on a branch with a clear message.

## Ask before doing

- Changing model weights, the star metric, or the `odds.json` shape.
- Adding any dependency or paid service.
- Anything that scrapes a new site.
- Making the repo private or changing deployment settings.
