"""Checks that a parsed odds.json follows the contract in SPEC.md section 7.

The site reads only data/odds.json, so nothing may be published unless it passes.
"""
import json
from datetime import datetime

MAX_BYTES = 100 * 1024  # SPEC.md section 7: file stays under 100 KB
EXPECTED_TEAMS = 30
MAX_HISTORY_DAYS = 30
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PHASES = ("regular_season", "playin", "playoffs", "complete")
HEADLINE_SOURCES = ("softmax", "simulation")
STATUSES = ("alive", "eliminated", "champion")


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
    problems += _check_market(odds)
    problems += _check_playoff_mode(odds)
    problems += _check_health(odds)

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


def _check_health(odds: dict) -> list[str]:
    """Checks for the optional injury fields (SPEC.md sections 6.7 and 7): per-team `health_adjustment` (a number)
    and `injuries` (a list of {player, status, expected_return (YYYY-MM-DD or null), playoff_credit in [0, 1],
    adjustment}). They only run when those fields are present."""
    problems: list[str] = []
    for team in odds["teams"]:
        tid = team.get("id")
        if "health_adjustment" in team:
            v = team["health_adjustment"]
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                problems.append(f"{tid} health_adjustment must be a number")
        for entry in team.get("injuries", []) if isinstance(team.get("injuries", []), list) else [None]:
            ok = isinstance(entry, dict) and isinstance(entry.get("player"), str) and isinstance(entry.get("status"), str)
            credit = entry.get("playoff_credit") if isinstance(entry, dict) else None
            ok = ok and isinstance(credit, (int, float)) and 0 <= credit <= 1
            ok = ok and isinstance(entry.get("adjustment"), (int, float))
            returns = entry.get("expected_return") if isinstance(entry, dict) else None
            if returns is not None:
                try:
                    datetime.strptime(returns, "%Y-%m-%d")
                except (TypeError, ValueError):
                    ok = False
            if not ok:
                problems.append(f"{tid} has a malformed injuries entry")
                break
    return problems


def _check_market(odds: dict) -> list[str]:
    """Checks for the optional market fields (SPEC.md section 6.7): top-level `market` {source, fetched_at,
    weight, used} and per-team `market_prob` / `model_prob`. They only run when present. The weight must be
    0 when the market was not used, and every team carries both probabilities when it was."""
    problems: list[str] = []
    block = odds.get("market")
    if block is not None:
        if not isinstance(block, dict):
            return ["market must be an object"]
        weight, used = block.get("weight"), block.get("used")
        if not isinstance(used, bool):
            problems.append("market.used must be true or false")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 <= weight <= 1:
            problems.append("market.weight must be a number between 0 and 1")
        elif used is False and weight != 0:
            problems.append("market.weight must be 0 when the market was not used")
        if used is True and not block.get("fetched_at"):
            problems.append("market.fetched_at is required when the market was used")
    for team in odds["teams"]:
        for key in ("market_prob", "model_prob"):
            if key in team and not (isinstance(team[key], (int, float)) and 0 <= team[key] <= 1):
                problems.append(f"{team.get('id')} {key} must be a number between 0 and 1")
        if isinstance(block, dict) and block.get("used") is True and ("market_prob" not in team or "model_prob" not in team):
            problems.append(f"{team.get('id')} needs market_prob and model_prob when the market was used")
    return problems


def _check_playoff_mode(odds: dict) -> list[str]:
    """Checks for the optional playoff-mode fields (SPEC.md section 6.6): `phase`, `headline_source`, per-team
    `status` and `softmax_prob`. When the headline comes from the simulation, title_prob must equal sim_prob,
    a team that is out must have exactly 0, and a champion exactly 1.0 (one champion, and only in the
    "complete" phase). They only run when those fields are present."""
    problems: list[str] = []
    phase, source = odds.get("phase"), odds.get("headline_source")
    if phase is not None and phase not in PHASES:
        problems.append(f"phase must be one of {', '.join(PHASES)}")
    if source is not None and source not in HEADLINE_SOURCES:
        problems.append(f"headline_source must be one of {', '.join(HEADLINE_SOURCES)}")
    blended = bool((odds.get("market") or {}).get("used"))     # the market blend moves title_prob off sim_prob
    champions = []
    for team in odds["teams"]:
        tid = team.get("id")
        status = team.get("status")
        if status is not None and status not in STATUSES:
            problems.append(f"{tid} status must be one of {', '.join(STATUSES)}")
        if "softmax_prob" in team and not (isinstance(team["softmax_prob"], (int, float)) and 0 <= team["softmax_prob"] <= 1):
            problems.append(f"{tid} softmax_prob must be a number between 0 and 1")
        if status == "champion":
            champions.append(tid)
        if source == "simulation":
            if not blended and team.get("sim_prob") != team.get("title_prob"):
                problems.append(f"{tid} title_prob must equal sim_prob when the headline comes from the simulation")
            if status == "eliminated" and team.get("title_prob") != 0:
                problems.append(f"{tid} is eliminated but has title odds")
            if status == "champion" and team.get("title_prob") != 1:
                problems.append(f"{tid} is the champion but its title_prob is not 1.0")
            if status is None:
                problems.append(f"{tid} has no status in playoff mode")
    if len(champions) > 1:
        problems.append("more than one champion")
    if phase == "complete" and len(champions) != 1:
        problems.append("phase is complete but there is not exactly one champion")
    if phase is not None and phase != "complete" and champions:
        problems.append("a champion is named but phase is not complete")
    if source == "simulation" and phase in (None, "regular_season"):
        problems.append("headline_source is simulation but phase is not a playoff phase")
    return problems
