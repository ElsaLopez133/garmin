"""Shared library for the cycle-phase / follicular->luteal transition analysis.

Modules
-------
hsmm            one-boundary HSMM: boundary search, Gaussian emissions, logmvn
emissions       anchor extraction + generative / discriminative emission fits
features        robust (median/MAD) standardization, global and per-person
truth_mcphases  mcPHASES hormonal ground truth (LH-peak ovulation, luteal onset)
"""
from pathlib import Path

from . import hsmm, emissions, features  # noqa: F401

# Repo root (cyclelib is installed editable from analysis/cyclelib), so notebooks can
# locate data/ whatever their working directory is.
REPO_ROOT = Path(__file__).resolve().parents[2]
