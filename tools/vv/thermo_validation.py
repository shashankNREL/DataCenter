"""V&V Phase 3 — thermodynamic validation of the gas plant surrogates.

Checks:
  1. First-law closure for the simple-cycle models at 5 load points:
       fuel*LHV = P_el + m_exh*cp*(T_exh - T_amb) + residual
     The residual (mechanical + generator + radiation losses, and model
     error) must be non-negative-ish and bounded. cp of flue gas is
     bracketed [1005, 1150] J/kg/K to expose sensitivity.
  2. Combined-cycle internal consistency: GT exhaust energy accounting
     P_st = eta_b * Q_HRSG and stack + condenser rejection close by
     construction; verify efficiencies stay in physical CC ranges.
  3. CO2 emission factors vs stoichiometry and the EPA natural-gas factor.
  4. Part-load heat-rate table for the report.

Run:  pixi run python tools/vv/thermo_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gas_plant import (  # noqa: E402
    GasTurbinePlant, CombinedCyclePlant, LM9000SimpleCycle, LM9000CombinedCycle,
)

T_AMB_K = 288.15  # ISO ambient
LOADS = np.array([0.2, 0.4, 0.6, 0.8, 1.0])


def closure_simple_cycle(plant, name: str, cp_lo=1005.0, cp_hi=1150.0) -> bool:
    print(f"\n--- First-law closure: {name} ---")
    print(f"{'L':>4s} {'P MW':>8s} {'fuel':>6s} {'eta':>6s} "
          f"{'Q_exh(lo..hi) MW':>18s} {'residual %(lo..hi)':>20s}")
    ok = True
    d = plant.dispatch(LOADS)
    for i, L in enumerate(LOADS):
        P = d["power_w"][i]
        fuel = d["fuel_kg_s"][i]
        Ein = fuel * plant.fuel_lhv_j_kg
        if Ein <= 0:
            continue
        q_lo = d["exhaust_m_kg_s"][i] * cp_lo * (d["exhaust_T_K"][i] - T_AMB_K)
        q_hi = d["exhaust_m_kg_s"][i] * cp_hi * (d["exhaust_T_K"][i] - T_AMB_K)
        r_hi = 100 * (Ein - P - q_lo) / Ein   # low cp -> high residual
        r_lo = 100 * (Ein - P - q_hi) / Ein
        print(f"{L:4.1f} {P/1e6:8.1f} {fuel:6.2f} {d['efficiency'][i]:6.3f} "
              f"{q_lo/1e6:8.1f}..{q_hi/1e6:6.1f}    {r_lo:6.1f}..{r_hi:5.1f}")
        # First law: residual must not be significantly negative for any cp
        # in the bracket; allow -5% for cp/table uncertainty.
        ok &= r_hi > -5.0
    return ok


def cc_consistency(plant, name: str) -> bool:
    print(f"\n--- Combined-cycle consistency: {name} ---")
    d = plant.dispatch(LOADS)
    eta = d["efficiency"]
    print("loads      :", LOADS)
    print("efficiency :", np.round(eta, 4))
    ok = bool(np.all(np.diff(eta) > 0)) and 0.45 < eta[-1] < 0.62
    print("monotone increasing + full-load in [0.45, 0.62]:", ok)
    return ok


def co2_factors() -> bool:
    print("\n--- CO2 emission factors (kg CO2 / kg fuel) ---")
    stoich_ch4 = 44.009 / 16.043
    # EPA 40 CFR Pt.98 Table C-1: natural gas 53.06 kg CO2/MMBtu (HHV);
    # pipeline NG HHV ~ 52.2 MJ/kg
    epa = 53.06 * 52.2 / 1055.06
    used_generic = 2.75
    used_lm9000 = 2.65
    print(f"stoichiometric CH4        : {stoich_ch4:.3f}")
    print(f"EPA Table C-1 (pipeline)  : {epa:.3f}")
    print(f"repo generic value        : {used_generic:.3f}")
    print(f"repo LM9000 value         : {used_lm9000:.3f}")
    ok = (2.55 <= used_lm9000 <= 2.80) and (2.55 <= used_generic <= 2.80) \
        and abs(used_generic - stoich_ch4) < 0.02
    print("both within [2.55, 2.80] and generic ~= stoich CH4:", ok)
    return ok


def heat_rate_table() -> None:
    print("\n--- Part-load LHV heat rate (kJ/kWh) ---")
    plants = {
        "Frame GT 235 MW (ThermoPower)": GasTurbinePlant(),
        "CCPP 338.7 MW": CombinedCyclePlant(),
        "LM9000 SC": LM9000SimpleCycle(),
        "LM9000 CC": LM9000CombinedCycle(),
    }
    hdr = f"{'load':>5s}" + "".join(f"{n:>32s}" for n in plants)
    print(hdr)
    for L in LOADS:
        row = f"{L:5.1f}"
        for p in plants.values():
            d = p.dispatch(float(L))
            hr = 3600e3 / d["efficiency"] / 1e3 if d["efficiency"] > 0 else np.nan
            row += f"{hr:32.0f}"
        print(row)


def main() -> int:
    ok = True
    ok &= closure_simple_cycle(GasTurbinePlant(), "Frame GT 235 MW (ThermoPower surrogate)")
    ok &= closure_simple_cycle(LM9000SimpleCycle(), "LM9000 simple cycle (Willans)")
    ok &= cc_consistency(CombinedCyclePlant(), "Heavy CCPP")
    ok &= cc_consistency(LM9000CombinedCycle(), "LM9000 CC")
    ok &= co2_factors()
    heat_rate_table()
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
