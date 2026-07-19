"""Build ANDES cases parameterized from gas_plant configurations.

This package depends on gas_plant; gas_plant does NOT import from here.
That keeps the steady-state surrogate usable standalone without dragging
in the ANDES dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import andes

from gas_plant import GasTurbinePlant, CombinedCyclePlant
from .defaults import (
    MachineDefaults,
    GovernorDefaults,
    ExciterDefaults,
    LineDefaults,
    GridDefaults,
    DEFAULT_SYSTEM_MVA_BASE,
    DEFAULT_BUS_KV,
)


@dataclass
class IslandedCaseConfig:
    """Inputs to build_islanded_test_case().

    Models a single gas plant at a local bus, a data-center constant-power
    load at a second bus, connected to an infinite grid through a tie line
    that can be opened (islanding) and re-closed (resync) via Toggle events.
    """

    # Plant — pass an already-constructed GasTurbinePlant or CombinedCyclePlant
    plant: object  # GasTurbinePlant | CombinedCyclePlant

    # Steady-state operating point used to seed the power flow
    plant_load_setpoint: float = 0.9   # GTLoad in [0, 1] before islanding

    # Data-center demand in MW (constant power; treated as `pq2z=0` in dynamics)
    data_center_mw: float = 200.0
    data_center_mvar: float = 40.0

    # System parameters
    bus_kv: float = DEFAULT_BUS_KV
    system_mva_base: float = DEFAULT_SYSTEM_MVA_BASE

    # Component defaults — override fields on these dataclasses if needed
    machine: MachineDefaults = field(default_factory=MachineDefaults)
    governor: GovernorDefaults = field(default_factory=GovernorDefaults)
    exciter: ExciterDefaults = field(default_factory=ExciterDefaults)
    local_line: LineDefaults = field(default_factory=LineDefaults)
    grid: GridDefaults = field(default_factory=GridDefaults)

    # Optional events (set to None to omit)
    island_time_s: Optional[float] = 2.0    # open grid tie
    resync_time_s: Optional[float] = 15.0   # close grid tie back

    # Optional list of (time_s, new_load_mw) — schedules step changes to
    # the data-center active-power demand during the simulation. Useful for
    # modeling workload surges during a grid outage.
    load_step_events: List[Tuple[float, float]] = field(default_factory=list)

    # Plant operating point at the moment of islanding — informational
    @property
    def plant_p_mw(self) -> float:
        return self.plant.dispatch(self.plant_load_setpoint)["power_w"] / 1e6


def _plant_rated_mw(plant) -> float:
    return float(plant.rated_power_mw)


def build_islanded_test_case(cfg: IslandedCaseConfig) -> andes.System:
    """Construct a 3-bus ANDES case: gas plant + data-center load + grid tie.

    Returns the ANDES System with PFlow already executed. Caller controls
    when to run TDS (e.g., via run_islanding_scenario in scenarios.py).
    """
    andes.config_logger(stream_level=40)

    ss = andes.System()

    # Per-unitize power on the system base
    p_plant_pu = cfg.plant_p_mw / cfg.system_mva_base
    p_load_pu = cfg.data_center_mw / cfg.system_mva_base
    q_load_pu = cfg.data_center_mvar / cfg.system_mva_base

    plant_rated_mva = _plant_rated_mw(cfg.plant)

    # --- buses
    ss.add('Bus', dict(idx='B_GAS',  name='Gas',         Vn=cfg.bus_kv))
    ss.add('Bus', dict(idx='B_DC',   name='DataCenter',  Vn=cfg.bus_kv))
    ss.add('Bus', dict(idx='B_GRID', name='Grid',        Vn=cfg.bus_kv))

    # --- lines
    ss.add('Line', dict(
        idx='L_LOCAL', bus1='B_GAS', bus2='B_DC',
        r=cfg.local_line.r, x=cfg.local_line.x, b=cfg.local_line.b,
        Vn1=cfg.bus_kv, Vn2=cfg.bus_kv,
    ))
    ss.add('Line', dict(
        idx='L_TIE', bus1='B_DC', bus2='B_GRID',
        r=cfg.grid.tie_r, x=cfg.grid.tie_x, b=cfg.grid.tie_b,
        Vn1=cfg.bus_kv, Vn2=cfg.bus_kv,
    ))

    # --- static gens for power-flow setup
    # Gas plant is the local PV bus, grid is the infinite Slack
    ss.add('PV', dict(
        idx='G_GAS', bus='B_GAS',
        p0=p_plant_pu, v0=1.0, Vn=cfg.bus_kv,
    ))
    ss.add('Slack', dict(
        idx='G_GRID', bus='B_GRID',
        p0=0.0, v0=1.0, Vn=cfg.bus_kv,
    ))

    # --- load
    ss.add('PQ', dict(
        idx='LD_DC', bus='B_DC',
        p0=p_load_pu, q0=q_load_pu, Vn=cfg.bus_kv,
    ))

    # --- dynamic models on the gas plant
    ss.add('GENCLS', dict(
        idx='SYN_GAS', bus='B_GAS', gen='G_GAS',
        Sn=plant_rated_mva,
        M=cfg.machine.M,
        D=cfg.machine.D,
    ))
    ss.add('TGOV1', dict(
        idx='TG_GAS', syn='SYN_GAS',
        R=cfg.governor.R,
        T1=cfg.governor.T1, T2=cfg.governor.T2, T3=cfg.governor.T3,
        Dt=cfg.governor.Dt,
        VMAX=cfg.governor.VMAX, VMIN=cfg.governor.VMIN,
    ))

    # --- frequency measurements (one per bus we care about)
    ss.add('BusFreq', dict(idx='F_GAS', bus='B_GAS'))
    ss.add('BusFreq', dict(idx='F_DC', bus='B_DC'))

    # --- events: open grid tie (island), then close (resync)
    if cfg.island_time_s is not None:
        ss.add('Toggle', dict(model='Line', dev='L_TIE', t=cfg.island_time_s))
    if cfg.resync_time_s is not None:
        ss.add('Toggle', dict(model='Line', dev='L_TIE', t=cfg.resync_time_s))

    # --- load step events (modify PQ.Ppf at scheduled times).
    # Note: tested with both `p0` (param) and `Ppf` (service). In ANDES 2.0
    # PQ params don't propagate to network equations mid-TDS; the right
    # writable target is the algebraic injection `Ppf`.
    for t_step, new_p_mw in cfg.load_step_events:
        new_p_pu = new_p_mw / cfg.system_mva_base
        ss.add('Alter', dict(
            model='PQ', dev='LD_DC', src='Ppf', attr='v',
            t=t_step, method='=', amount=new_p_pu,
        ))

    ss.setup()
    ss.PFlow.run()
    if not ss.PFlow.converged:
        raise RuntimeError("Power flow did not converge — check case setup")
    return ss


def build_islanded_case_v2(cfg: IslandedCaseConfig,
                           power_factor: float = 0.85,
                           allow_unsynchronized_reclose: bool = False) -> andes.System:
    """V&V Phase 3 upgrade of the islanded case.

    Changes vs `build_islanded_test_case` (all found in the V&V critique):
      - **GENROU** round-rotor machine (6th order) instead of GENCLS, with
        **EXST1** exciter (the v1 `ExciterDefaults` were dead code: GENCLS
        cannot take an exciter, so v1 bus voltages were constant-flux
        artifacts).
      - **Sn = rated MW / power_factor** (v1 used MW as MVA, silently
        inflating the effective machine inertia base by 1/PF).
      - **TGOV1 VMAX on the turbine rating**: VMAX = (rated MW)/Sn = PF pu on
        the machine base, replacing the v1 VMAX=1.2 (20 % steady overload).
      - **Reclose guard**: ANDES `Toggle` recloses the tie regardless of the
        angle/frequency difference across the open breaker — physically a
        potentially catastrophic out-of-phase reclosure with no sync-check
        relay modeled. The resync event is therefore DISABLED unless the
        caller passes `allow_unsynchronized_reclose=True`.
      - PES-TR1-caveat: ANDES 2.0 ships no GGOV1; TGOV1 is the closest
        available standard governor. The scipy GGOV1 (gas_plant.dynamics)
        remains the reference LM2500 governor.
    """
    andes.config_logger(stream_level=40)

    ss = andes.System()

    p_plant_pu = cfg.plant_p_mw / cfg.system_mva_base
    p_load_pu = cfg.data_center_mw / cfg.system_mva_base
    q_load_pu = cfg.data_center_mvar / cfg.system_mva_base

    rated_mw = _plant_rated_mw(cfg.plant)
    sn_mva = rated_mw / power_factor
    vmax_machine_base = rated_mw / sn_mva  # = power_factor

    ss.add('Bus', dict(idx='B_GAS', name='Gas', Vn=cfg.bus_kv))
    ss.add('Bus', dict(idx='B_DC', name='DataCenter', Vn=cfg.bus_kv))
    ss.add('Bus', dict(idx='B_GRID', name='Grid', Vn=cfg.bus_kv))

    ss.add('Line', dict(idx='L_LOCAL', bus1='B_GAS', bus2='B_DC',
                        r=cfg.local_line.r, x=cfg.local_line.x,
                        b=cfg.local_line.b, Vn1=cfg.bus_kv, Vn2=cfg.bus_kv))
    ss.add('Line', dict(idx='L_TIE', bus1='B_DC', bus2='B_GRID',
                        r=cfg.grid.tie_r, x=cfg.grid.tie_x, b=cfg.grid.tie_b,
                        Vn1=cfg.bus_kv, Vn2=cfg.bus_kv))

    ss.add('PV', dict(idx='G_GAS', bus='B_GAS', p0=p_plant_pu, v0=1.0,
                      Vn=cfg.bus_kv))
    ss.add('Slack', dict(idx='G_GRID', bus='B_GRID', p0=0.0, v0=1.0,
                         Vn=cfg.bus_kv))
    ss.add('PQ', dict(idx='LD_DC', bus='B_DC', p0=p_load_pu, q0=q_load_pu,
                      Vn=cfg.bus_kv))

    # GENROU with textbook round-rotor parameters (Kundur Table 4.2 class)
    ss.add('GENROU', dict(
        idx='SYN_GAS', bus='B_GAS', gen='G_GAS',
        Sn=sn_mva, Vn=cfg.bus_kv,
        M=2.0 * cfg.machine.H_s, D=cfg.machine.D,
        xd=1.8, xq=1.7, xd1=0.3, xq1=0.55, xd2=0.25, xq2=0.25,
        xl=0.15, ra=0.0,
        Td10=8.0, Tq10=0.4, Td20=0.03, Tq20=0.05,
    ))
    ss.add('TGOV1', dict(
        idx='TG_GAS', syn='SYN_GAS',
        R=cfg.governor.R,
        T1=cfg.governor.T1, T2=cfg.governor.T2, T3=cfg.governor.T3,
        Dt=cfg.governor.Dt,
        VMAX=vmax_machine_base, VMIN=cfg.governor.VMIN,
    ))
    ss.add('EXST1', dict(
        idx='EX_GAS', syn='SYN_GAS',
        TR=cfg.exciter.TR, KA=cfg.exciter.KA, TA=cfg.exciter.TA,
        VRMAX=cfg.exciter.VRMAX, VRMIN=cfg.exciter.VRMIN,
    ))

    ss.add('BusFreq', dict(idx='F_GAS', bus='B_GAS'))
    ss.add('BusFreq', dict(idx='F_DC', bus='B_DC'))

    if cfg.island_time_s is not None:
        ss.add('Toggle', dict(model='Line', dev='L_TIE', t=cfg.island_time_s))
    if cfg.resync_time_s is not None:
        if allow_unsynchronized_reclose:
            ss.add('Toggle', dict(model='Line', dev='L_TIE',
                                  t=cfg.resync_time_s))
        else:
            import warnings
            warnings.warn(
                "resync_time_s set but no sync-check relay is modeled; "
                "an out-of-phase reclosure would be a catastrophic event. "
                "Pass allow_unsynchronized_reclose=True to force it "
                "(results after reclose are then illustrative only).")

    for t_step, new_p_mw in cfg.load_step_events:
        new_p_pu = new_p_mw / cfg.system_mva_base
        ss.add('Alter', dict(model='PQ', dev='LD_DC', src='Ppf', attr='v',
                             t=t_step, method='=', amount=new_p_pu))

    ss.setup()
    ss.PFlow.run()
    if not ss.PFlow.converged:
        raise RuntimeError("Power flow did not converge — check case setup")
    return ss
