"""Phase 2 stand-in for the real pipeline: refresh `generated_at` in data/odds.json.

It proves the automation loop (daily schedule -> update file -> commit -> live site)
before any real modeling exists. The odds themselves are untouched and stay marked
`is_sample`. Phase 4 replaces this with pipeline/build_odds.py.

Run:  python pipeline/refresh_timestamp.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a plain script (python pipeline/refresh_timestamp.py) as well as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.validate import TIMESTAMP_FORMAT, validate_odds  # noqa: E402

ODDS_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.json"


def refresh(path: Path = ODDS_PATH, now: datetime | None = None) -> str:
    """Set generated_at to `now` (default: the current UTC time) and save the file.

    Returns the new timestamp. Raises ValueError, leaving the file untouched,
    if the file is invalid before or after the change.
    """
    original = path.read_text()
    odds = json.loads(original)

    problems = validate_odds(odds)
    if problems:
        raise ValueError("odds.json is already invalid: " + "; ".join(problems))

    stamp = (now or datetime.now(timezone.utc)).strftime(TIMESTAMP_FORMAT)
    odds["generated_at"] = stamp

    problems = validate_odds(odds)
    if problems:
        raise ValueError("refreshed odds.json would be invalid: " + "; ".join(problems))

    text = json.dumps(odds, indent=1)
    if original.endswith("\n"):
        text += "\n"
    path.write_text(text)
    return stamp


def main() -> int:
    """Command-line entry point. Returns the process exit code (non-zero fails the Action)."""
    try:
        stamp = refresh()
    except (OSError, ValueError) as err:
        print(f"ERROR: could not refresh {ODDS_PATH}: {err}", file=sys.stderr)
        return 1
    print(f"Refreshed {ODDS_PATH.name}: generated_at = {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
