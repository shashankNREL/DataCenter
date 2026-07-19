"""Dynamic models for the LM-class gas turbines.

Each module is a self-contained ODE-based dynamic model of the prime mover +
governor, intended for islanded transient studies (frequency, torque, fuel)
driven by an externally imposed load profile.

Modules:
    tgov1  - Tier A: cleaned-up TGOV1 (anti-windup, rate limits, load damping,
             fuel-from-valve, event-driven integration). Drop-in replacement
             for the inline ODE in notebooks/lm2500_model.ipynb cells 25-27.

    ggov1  - Tier B: IEEE PES-TR1 GGOV1 governor (turbine-MW base) —
             the REFERENCE governor model.
    multishaft - Tier C: two-mass gas-path model on top of GGOV1.
    torsional  - Tier E: 2-mass PT-gen torsional shaft + rainflow/Miner.
    rowen  - Rowen/Hannett Fig-1/2 model, for literature cross-validation
             only (tools/vv/validate_hannett.py).
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
from .rowen import (
    RowenParams,
    RowenResult,
    simulate_rowen,
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
    "RowenParams",
    "RowenResult",
    "simulate_rowen",
    "TorsionalParams",
    "compute_shaft_torques",
    "rainflow_count",
    "miners_damage",
    "detrend_rolling_median",
    "section_modulus_torsion_m3",
]
