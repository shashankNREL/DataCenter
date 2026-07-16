"""Default machine, governor, and exciter parameters for gas-fired generators.

Values are textbook standards from power-system stability literature
(Kundur, IEEE 421.5). They are *not* in `gas_plant` because the steady-
state surrogate doesn't need them — they're only relevant for transient
electrical dynamics.

Override any of these per-case via the corresponding dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MachineDefaults:
    """Classical synchronous-machine parameters (GENCLS)."""

    # Inertia constant H in seconds. Typical heavy-duty gas turbine: 4-6 s.
    # ANDES GENCLS uses M = 2H.
    H_s: float = 5.0

    # Damping coefficient D (pu/pu). Typical 0-5; 2 is a moderate value
    # accounting for some load damping and machine damping windings.
    D: float = 2.0

    @property
    def M(self) -> float:
        return 2.0 * self.H_s


@dataclass
class GovernorDefaults:
    """TGOV1 governor parameters.

    Reasonable for a heavy-duty gas turbine with a standard speed governor +
    fuel-valve actuator + thermal lag. T1 / T2 / T3 are the lag/lead time
    constants of TGOV1's first-order-lag-lead block followed by a first-
    order lag for the steam (here: combustion-gas) volume.
    """

    R: float = 0.05   # droop (5 %)
    T1: float = 0.5   # speed governor lag (s)
    T2: float = 1.0   # lead time constant (s)
    T3: float = 5.0   # turbine time constant (s) — slower for thermal mass
    Dt: float = 0.0   # turbine damping
    VMAX: float = 1.2 # max valve / fuel position (pu of rated)
    VMIN: float = 0.0


@dataclass
class ExciterDefaults:
    """EXST1 (IEEE Type ST1) exciter parameters."""

    TR: float = 0.01   # voltage transducer time constant (s)
    KA: float = 200.0  # main regulator gain
    TA: float = 0.02   # regulator time constant (s)
    VRMAX: float = 5.0
    VRMIN: float = -5.0


@dataclass
class LineDefaults:
    """Per-unit line parameters on system MVA base (default 100 MVA)."""

    r: float = 0.001
    x: float = 0.01
    b: float = 0.0


@dataclass
class GridDefaults:
    """Grid-tie / infinite bus defaults."""

    # When the data-center bus is far from a strong grid node, the tie has
    # higher impedance. Default reflects ~10 km of 230 kV transmission.
    tie_r: float = 0.001
    tie_x: float = 0.05
    tie_b: float = 0.0


# A 100 MVA system base is ANDES's default; we expose it here so callers can
# choose other bases consistently across machine and line per-unitization.
DEFAULT_SYSTEM_MVA_BASE = 100.0
DEFAULT_BUS_KV = 20.0
DEFAULT_NOMINAL_FREQ_HZ = 60.0
