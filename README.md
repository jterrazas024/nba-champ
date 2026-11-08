# NBA Championship Odds

A live odds board showing each NBA team's estimated probability of winning the 2026-27 championship. A Python pipeline computes the odds from public NBA stats and writes `data/odds.json`; a static site on GitHub Pages renders it, and a scheduled GitHub Action refreshes the data daily.

Model estimates for information only. Not betting advice.

See `SPEC.md` for the full specification and `docs/research-plan.md` for the research behind the model.

**Live site:** https://jterrazas024.github.io/nba-champ/

**Status:** Phases 0-3 done: the sample-data odds board is live and refreshes daily, and the data source (Basketball-Reference) is chosen and tested from GitHub Actions. Next: build the real model (Phase 4). Odds shown are sample data until then.

**Run locally:** `pip install -r requirements.txt`, `pytest`, then `python -m http.server 8000` and open http://localhost:8000.

