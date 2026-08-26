"""Test bootstrap.

``cyclelib`` and ``preprocessing`` are importable via the installed project
(`uv sync` / `pip install -e .`). ``analysis/combine_participants.py`` is a loose
script, not a packaged module, so add ``analysis/`` to the path for the tests that
exercise it.
"""
import sys
from pathlib import Path

ANALYSIS_DIR = Path(__file__).resolve().parent.parent / "analysis"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
