"""Checks that a parsed odds.json follows the contract in SPEC.md section 7.

The site reads only data/odds.json, so nothing may be published unless it passes.
"""
import json
from datetime import datetime

MAX_BYTES = 100 * 1024  # SPEC.md section 7: file stays under 100 KB
EXPECTED_TEAMS = 30
MAX_HISTORY_DAYS = 30
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def validate_odds(odds: dict) -> list[str]:
    """Return a list of problems with `odds` (an empty list means the file is valid)."""
    problems: list[str] = []

    for key in ("generated_at", "season", "is_sample", "weights", "teams"):
        if key not in odds:
            problems.append(f"missing top-level field: {key}")
    if problems:
        return problems  # the checks below need these fields

    try:
        datetime.strptime(odds["generated_at"], TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        problems.append(f"generated_at is not a UTC timestamp like 2026-11-08T12:00:00Z: {odds['generated_at']!r}")

    if not isinstance(odds["is_sample"], bool):
        problems.append("is_sample must be true or false")

    if abs(sum(odds["weights"].values()) - 1.0) > 1e-9:
        problems.append("model weights do not sum to 1.0")

    teams = odds["teams"]
    if len(teams) != EXPECTED_TEAMS:
        problems.append(f"expected {EXPECTED_TEAMS} teams, found {len(teams)}")
    if len({t.get("id") for t in teams}) != len(teams):
        problems.append("team ids are not unique")

    probs = [t.get("title_prob") for t in teams]
    if not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in probs):
        problems.append("every title_prob must be a number between 0 and 1")
    elif abs(sum(probs) - 1.0) > 1e-6:
        problems.append(f"title probabilities sum to {sum(probs):.6f}, not 1.0")

    ranked = sorted(teams, key=lambda t: t.get("rank", 0))
    if [t.get("rank") for t in ranked] != list(range(1, len(teams) + 1)):
        problems.append("ranks must run 1 to N with no gaps or repeats")
    elif [t["title_prob"] for t in ranked] != sorted((t["title_prob"] for t in ranked), reverse=True):
        problems.append("rank 1 must have the highest title_prob, and so on down")

    problems += _check_history(odds)
    problems += check_sim(odds)

    if len(json.dumps(odds, indent=1).encode()) >= MAX_BYTES:
        problems.append("file would be 100 KB or larger")

    return problems


def _check_history(odds: dict) -> list[str]:
    """Checks for the optional `history_dates` / `history` fields (SPEC.md section 7); they only run
    when those fields are present, so older files still validate."""
    problems: list[str] = []
    dates = odds.get("history_dates")
    if dates is not None:
        if not isinstance(dates, list) or not all(isinstance(d, str) for d in dates):
            return ["history_dates must be a list of YYYY-MM-DD strings"]
        try:
            for d in dates:
                if datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m-%d") != d:
                    raise ValueError(d)
        except ValueError:
            problems.append("history_dates must be valid YYYY-MM-DD dates")
        if any(a >= b for a, b in zip(dates, dates[1:])):
            problems.append("history_dates must be strictly increasing")
        if len(dates) > MAX_HISTORY_DAYS:
            problems.append(f"history_dates has {len(dates)} entries, at most {MAX_HISTORY_DAYS} allowed")
    for team in odds["teams"]:
        if "history" not in team:
            continue
        history = team["history"]
        if dates is None:
            problems.append(f"{team.get('id')} has history but the file has no history_dates")
        elif not isinstance(history, list) or len(history) != len(dates):
            problems.append(f"{team.get('id')} history length does not match history_dates")
        elif not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in history):
            problems.append(f"{team.get('id')} history values must be numbers between 0 and 1")
    return problems


def check_sim(odds: dict) -> list[str]:
    """Checks for the optional simulation fields (SPEC.md section 7, shadow mode): per-team `sim_prob` and
    `sim`, and the top-level `sim`. They only run when those fields are present."""
    problems: list[str] = []
    top = odds.get("sim")
    if top is not None:
        runs = top.get("runs") if isinstance(top, dict) else None
        if not isinstance(runs, int) or isinstance(runs, bool) or runs <= 0:
            problems.append("sim.runs must be a positive integer")
    def number(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    probs = []
    for team in odds["teams"]:
        tid = team.get("id")
        if "sim_prob" in team:
            p = team["sim_prob"]
            if not number(p) or not 0 <= p <= 1:
                problems.append(f"{tid} sim_prob must be a number between 0 and 1")
            else:
                probs.append(p)
        detail = team.get("sim")
        if detail is None:
            continue
        for key in ("finals", "conf_finals", "make_playoffs"):
            v = detail.get(key) if isinstance(detail, dict) else None
            if not number(v) or not 0 <= v <= 1:
                problems.append(f"{tid} sim.{key} must be a number between 0 and 1")
        seed_avg = detail.get("seed_avg") if isinstance(detail, dict) else None
        if seed_avg is not None and (not number(seed_avg) or not 1 <= seed_avg <= 15):
            problems.append(f"{tid} sim.seed_avg must be null or a number between 1 and 15")
    if probs and (len(probs) != len(odds["teams"]) or abs(sum(probs) - 1.0) > 1e-6):
        problems.append(f"sim_prob values sum to {sum(probs):.6f} over {len(probs)} teams, not 1.0 over every team")
    return problems
