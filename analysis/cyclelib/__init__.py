"""Shared library for the cycle-phase / follicular->luteal transition analysis.

Modules
-------
hsmm            one-boundary HSMM: boundary search, Gaussian emissions, logmvn
emissions       anchor extraction + generative / discriminative emission fits
features        robust (median/MAD) standardization, global and per-person
truth_mcphases  mcPHASES hormonal ground truth (LH-peak ovulation, luteal onset)
"""
from . import hsmm, emissions, features  # noqa: F401
