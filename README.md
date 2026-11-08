# NBA Championship Odds

A live odds board showing each NBA team's estimated probability of winning the 2026-27 championship. A Python pipeline computes the odds from public NBA stats and writes `data/odds.json`; a static site on GitHub Pages renders it, and a scheduled GitHub Action refreshes the data daily.

Model estimates for information only. Not betting advice.

See `SPEC.md` for the full specification and `docs/research-plan.md` for the research behind the model.

**Live site:** https://jterrazas024.github.io/nba-champ/

**Status:** Phase 1 done (sample-data odds board is live). Odds shown are sample data until the model is built.

**Run locally:** `pip install -r requirements.txt`, `pytest`, then `python -m http.server 8000` and open http://localhost:8000.

