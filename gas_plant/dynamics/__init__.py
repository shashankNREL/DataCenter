"""Dynamic models for the LM-class gas turbines.

Each module is a self-contained ODE-based dynamic model of the prime mover +
governor, intended for islanded transient studies (frequency, torque, fuel)
driven by an externally imposed load profile.

Modules:
    tgov1  - Tier A: cleaned-up TGOV1 (anti-windup, rate limits, load damping,
             fuel-from-valve, event-driven integration). Drop-in replacement
             for the inline ODE in notebooks/lm2500_model.ipynb cells 25-27.

Future modules:
    ggov1       - Tier B: IEEE PES-TR1 GGOV1 governor.
    multishaft  - Tier C: two-mass shaft + single-axis machine via ANDES.
    torsional   - Tier E: three-mass torsional shaft for fatigue.
"""

from .tgov1 import (
    TGOV1Params,
    TGOV1State,
    TGOV1Result,
    simulate_tgov1,
)
from .ggov1 import (
    GGOV1Params,
    GGOV1State,
    GGOV1Result,
    simulate_ggov1,
    step_response,
)
from .multishaft import (
    MultishaftParams,
    MultishaftState,
    MultishaftResult,
    simulate_multishaft,
)
from .torsional import (
    TorsionalParams,
    compute_shaft_torques,
    rainflow_count,
    miners_damage,
    detrend_rolling_median,
    section_modulus_torsion_m3,
)

__all__ = [
    "TGOV1Params",
    "TGOV1State",
    "TGOV1Result",
    "simulate_tgov1",
    "GGOV1Params",
    "GGOV1State",
    "GGOV1Result",
    "simulate_ggov1",
    "step_response",
    "MultishaftParams",
    "MultishaftState",
    "MultishaftResult",
    "simulate_multishaft",
    "TorsionalParams",
    "compute_shaft_torques",
    "rainflow_count",
    "miners_damage",
    "detrend_rolling_median",
    "section_modulus_torsion_m3",
]
