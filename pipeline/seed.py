"""A repeatable random seed for the daily simulation (SPEC.md section 6.4)."""
import hashlib
from datetime import date


def daily_seed(day: date, season: str, model_version: str) -> int:
    """Return a 64-bit integer seed that depends only on the date, season label and model version.

    The same inputs always give the same seed (so a re-run on the same day reproduces the same
    odds), and a new day gives a new one. It uses SHA-256, not Python's built-in hash(), because
    hash() of a string changes from one Python process to the next.
    """
    text = f"{day.isoformat()}|{season}|{model_version}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
