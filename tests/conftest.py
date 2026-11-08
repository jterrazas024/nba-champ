"""Test-wide settings."""
from pipeline import config

# Use a small number of simulated seasons so the many end-to-end builds stay fast. This is set at
# import time (not in a fixture) because module-scoped fixtures build odds before function fixtures run.
# tests/test_config.py checks the production value in the source file.
config.SIM_RUNS = 400
