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
- Refresh timestamp (Phase 2 stand-in, run daily by the Action): `python pipeline/refresh_timestamp.py`
- Build odds (Phase 4, not written yet): `python pipeline/build_odds.py`
- Run the refresh Action by hand: `gh workflow run refresh.yml`, then `gh run list --workflow refresh.yml`
- Run tests: `pytest`
- Preview site: `python -m http.server 8000` then open http://localhost:8000
  (opening `index.html` by double-click will not load `odds.json` because of browser rules, so use the local server)

## The contract: `data/odds.json`

The site reads only this file. The pipeline is the only thing that writes it. Do not change its shape without updating `SPEC.md` (section "odds.json schema"), the pipeline, the site, and the tests together. Required rules:

- Title probabilities across all 30 teams sum to 1.0 (tests must check this).
- Includes `generated_at` (UTC ISO timestamp) so the site can show "Last updated".
- File stays under 100 KB.

## Model rules

- Starting weights live in one place (`pipeline/config.py`) and must sum to 1.0:
  net rating 38%, star power 25%, recent form 10%, star availability 10%, healthy/core net rating 10%, rotation availability 5%, clutch 2%.
- If an input is not available yet, renormalize the remaining weights rather than filling with fake numbers.
- Early season: blend current-season stats with a preseason prior using `w_current = games_played / (games_played + 20)`.
- Star metric is BPM for now. Don't swap it without asking.
- Never present placeholder or fake data as real. Fake data is only for Phase 1 and must be labeled `"is_sample": true` in `odds.json`.

## Data source rules

- Basketball-Reference: **never exceed 20 requests per minute** (a violation blocks the IP for up to a day). Pace requests (one every ~4 seconds), cache every page under `data/raw/`, and never re-fetch what is already cached for the day.
- `stats.nba.com` / `nba_api`: blocks cloud IPs including GitHub Actions. Test from the laptop and from Actions before depending on it.
- `cdn.nba.com`: works from anywhere.
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
- Charts: hand-built SVG in `charts.js` (no libraries). One color per series, thin bars, hover/keyboard tooltips, and a table view for each chart.
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
