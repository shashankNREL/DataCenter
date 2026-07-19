"""End-to-end smoke test for the gas_plant package.

Exercises the public API exactly as the larger tool would. No Modelica /
OpenModelica / FMU runtime is touched — the package is pure Python over
the bundled surrogate CSV.

Run with the `datacenter` conda env:
    ~/miniconda3/envs/datacenter/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make `gas_plant` importable from the test location
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gas_plant import GasTurbinePlant, CombinedCyclePlant, LM9000SimpleCycle, LM9000CombinedCycle, Fleet


def test_scalar_dispatch_gt():
    gt = GasTurbinePlant()
    r = gt.dispatch(0.7)
    assert isinstance(r["power_w"], float)
    assert r["power_w"] > 0
    assert r["fuel_kg_s"] > 0
    assert 0 < r["efficiency"] < 0.5
    assert r["exhaust_T_K"] > 700
    assert r["co2_kg_s"] == r["fuel_kg_s"] * gt.co2_per_fuel_kg
    print(f"  GT @ 0.7: {r['power_w']/1e6:.1f} MW, eff={r['efficiency']:.3f}")


def test_scalar_dispatch_ccpp():
    ccpp = CombinedCyclePlant()
    r = ccpp.dispatch(1.0)
    assert 0.55 < r["efficiency"] < 0.62, f"got {r['efficiency']}"
    assert r["power_w"] > 300e6  # default CCPP is ~339 MW rated
    print(f"  CCPP @ 1.0: {r['power_w']/1e6:.1f} MW, eff={r['efficiency']:.3f}")


def test_array_dispatch():
    gt = GasTurbinePlant()
    loads = np.array([0.3, 0.6, 0.9])
    r = gt.dispatch(loads)
    assert r["power_w"].shape == (3,)
    assert np.all(np.diff(r["power_w"]) > 0)  # monotone in load
    print(f"  GT array dispatch: {r['power_w'] / 1e6}")


def test_dispatch_profile():
    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    profile = pd.Series(np.linspace(0.4, 1.0, 24), index=idx)
    gt = GasTurbinePlant()
    df = gt.dispatch_profile(profile)
    assert df.shape == (24, 6)
    assert df.index.equals(idx)
    assert df["power_w"].iloc[-1] > df["power_w"].iloc[0]
    print(f"  Profile: peak={df['power_w'].max()/1e6:.1f} MW over 24h")


def test_fleet_homogeneous():
    fleet = Fleet([GasTurbinePlant() for _ in range(4)])
    r = fleet.dispatch(0.8)
    assert len(r["units"]) == 4
    total_unit_power = sum(u["power_w"] for u in r["units"])
    assert abs(r["power_w"] - total_unit_power) < 1e-6
    print(f"  Homogeneous Fleet (4 GTs) @ 0.8: {r['power_w']/1e6:.1f} MW")


def test_fleet_heterogeneous_mixed_types():
    fleet = Fleet([GasTurbinePlant(), CombinedCyclePlant()])
    r = fleet.dispatch(np.array([0.6, 0.9]))
    assert len(r["units"]) == 2
    assert r["power_w"] > 0
    print(f"  Mixed Fleet [GT@0.6, CCPP@0.9]: {r['power_w']/1e6:.1f} MW, "
          f"eff={r['efficiency']:.3f}")


def test_rejects_bad_inputs():
    gt = GasTurbinePlant()
    for bad in [-0.1, 1.1, float("nan")]:
        try:
            gt.dispatch(bad)
        except ValueError:
            continue
        raise AssertionError(f"Expected ValueError for load={bad}")
    print("  Bad inputs (out-of-range, NaN) correctly rejected.")


def test_package_data_bundled():
    """The surrogate CSV must live inside the package, not at the project root."""
    from gas_plant.unit import _DEFAULT_TABLE_PATH
    assert _DEFAULT_TABLE_PATH.is_file(), f"missing: {_DEFAULT_TABLE_PATH}"
    assert "gas_plant" in _DEFAULT_TABLE_PATH.parts
    print(f"  Bundled surrogate found at {_DEFAULT_TABLE_PATH}")


def test_lm9000_simple_cycle_full_load():
    """Validate LM9000 simple cycle against GE datasheet.
    
    Datasheet anchor (Table 2):
      - Net Power: 56.723 MW
      - LHV Net Eff: 39.52%
      - LHV Heat Rate: 9,109 kJ/kW-hr
      - Exhaust: 456°C (729 K)
      - Specific CO2: 492.9 kg/MWh
    """
    lm = LM9000SimpleCycle()
    r = lm.dispatch(1.0)
    
    power_mw = r["power_w"] / 1e6
    efficiency = r["efficiency"]
    exhaust_t_k = r["exhaust_T_K"]
    fuel_kg_s = r["fuel_kg_s"]
    
    # Heat rate from efficiency (reverse relationship: HR = 3600 / efficiency in kJ/kWh)
    heat_rate_kj_kwh = 3600.0 / efficiency if efficiency > 0 else float("inf")
    
    # Specific CO2 from fuel and efficiency
    co2_per_mwh = (heat_rate_kj_kwh / 1000.0) * (fuel_kg_s / (r["power_w"] / 1e6)) * lm.co2_per_fuel_kg * 1000.0
    
    # Acceptance criteria (within 1.5% of datasheet)
    assert abs(power_mw - 56.723) / 56.723 < 0.015, \
        f"Power {power_mw:.2f} MW vs datasheet 56.723 MW"
    assert abs(efficiency - 0.3952) / 0.3952 < 0.015, \
        f"Efficiency {efficiency:.4f} vs datasheet 0.3952"
    assert abs(exhaust_t_k - 729.0) < 10.0, \
        f"Exhaust temp {exhaust_t_k:.0f} K vs datasheet 729 K"
    
    # Heat rate check (should be ~9109 kJ/kWh)
    assert abs(heat_rate_kj_kwh - 9109.0) / 9109.0 < 0.02, \
        f"Heat rate {heat_rate_kj_kwh:.0f} kJ/kWh vs datasheet 9,109"
    
    print(f"  LM9000 @ 1.0 (full load):")
    print(f"    Power: {power_mw:.2f} MW (target 56.723)")
    print(f"    Eff: {efficiency:.4f} (target 0.3952)")
    print(f"    Heat rate: {heat_rate_kj_kwh:.0f} kJ/kWh (target 9,109)")
    print(f"    Exhaust T: {exhaust_t_k:.0f} K (target 729 K)")
    print(f"    CO2: {lm.co2_per_fuel_kg:.2f} kg/kg fuel")


def test_lm9000_part_load():
    """Verify LM9000 part-load behavior is monotonic and smooth."""
    lm = LM9000SimpleCycle()
    loads = np.array([0.2, 0.4, 0.6, 0.8, 1.0])
    r = lm.dispatch(loads)
    
    # Power should be monotone increasing
    assert np.all(np.diff(r["power_w"]) >= 0), "Power not monotone increasing"
    
    # Fuel flow should be monotone increasing
    assert np.all(np.diff(r["fuel_kg_s"]) >= 0), "Fuel flow not monotone increasing"
    
    # Efficiency should stay in reasonable range
    assert np.all(r["efficiency"] > 0.2) and np.all(r["efficiency"] < 0.45), \
        f"Efficiency out of range: {r['efficiency']}"
    
    print(f"  LM9000 part-load sweep:")
    for load, p, eff in zip(loads, r["power_w"] / 1e6, r["efficiency"]):
        print(f"    Load {load:.1f}: {p:.1f} MW, eff {eff:.4f}")


def test_lm9000_fleet_compatibility():
    """Verify LM9000 works seamlessly in Fleet with other plant types."""
    fleet = Fleet([LM9000SimpleCycle(), GasTurbinePlant()])
    r = fleet.dispatch(np.array([0.8, 0.7]))
    
    assert r["power_w"] > 0
    assert len(r["units"]) == 2
    print(f"  Mixed Fleet [LM9000@0.8, GT235@0.7]: {r['power_w']/1e6:.1f} MW, "
          f"eff={r['efficiency']:.3f}")


def test_lm9000_combined_cycle_full_load():
    """Validate LM9000 combined cycle against GE datasheet.
    
    Datasheet anchor (Table 2, Combined Cycle):
      - Net Power: 72.471 MW
      - LHV Net Eff: 50.48%
      - LHV Heat Rate: 7,132 kJ/kW-hr
      - Specific CO2: 383.6 kg/MWh
    """
    ccpp = LM9000CombinedCycle()
    r = ccpp.dispatch(1.0)
    
    power_mw = r["power_w"] / 1e6
    efficiency = r["efficiency"]
    
    # Heat rate from efficiency
    heat_rate_kj_kwh = 3600.0 / efficiency if efficiency > 0 else float("inf")
    
    # Acceptance criteria (within 1.5% of datasheet)
    assert abs(power_mw - 72.471) / 72.471 < 0.015, \
        f"Power {power_mw:.2f} MW vs datasheet 72.471 MW"
    assert abs(efficiency - 0.5048) / 0.5048 < 0.015, \
        f"Efficiency {efficiency:.4f} vs datasheet 0.5048"
    
    # Heat rate check (should be ~7132 kJ/kWh)
    assert abs(heat_rate_kj_kwh - 7132.0) / 7132.0 < 0.02, \
        f"Heat rate {heat_rate_kj_kwh:.0f} kJ/kWh vs datasheet 7,132"
    
    print(f"  LM9000 Combined Cycle @ 1.0 (full load):")
    print(f"    Power: {power_mw:.2f} MW (target 72.471)")
    print(f"    Eff: {efficiency:.4f} (target 0.5048)")
    print(f"    Heat rate: {heat_rate_kj_kwh:.0f} kJ/kWh (target 7,132)")


def test_lm9000_combined_cycle_part_load():
    """Verify combined cycle part-load behavior."""
    ccpp = LM9000CombinedCycle()
    loads = np.array([0.2, 0.4, 0.6, 0.8, 1.0])
    r = ccpp.dispatch(loads)
    
    # Power should be monotone increasing
    assert np.all(np.diff(r["power_w"]) >= 0), "Power not monotone increasing"
    
    # Efficiency should degrade toward part load (Willans-line GT, V&V fix
    # P1) and stay in a physical CC range. The former assertion (> 0.4 at
    # 20 % load) encoded the old, unphysically flat quadratic.
    assert np.all(np.diff(r["efficiency"]) > 0), "CC efficiency not increasing with load"
    assert np.all(r["efficiency"] > 0.30) and np.all(r["efficiency"] < 0.55), \
        f"Efficiency out of range: {r['efficiency']}"
    assert 0.50 < r["efficiency"][-1] < 0.51, "full-load CC efficiency drifted"
    
    print(f"  LM9000 Combined Cycle part-load sweep:")
    for load, p, eff in zip(loads, r["power_w"] / 1e6, r["efficiency"]):
        print(f"    Load {load:.1f}: {p:.1f} MW, eff {eff:.4f}")


def test_lm9000_simple_vs_combined():
    """Compare simple cycle vs combined cycle at same load."""
    simple = LM9000SimpleCycle()
    combined = LM9000CombinedCycle()
    
    r_simple = simple.dispatch(1.0)
    r_combined = combined.dispatch(1.0)
    
    simple_eff = r_simple["efficiency"]
    combined_eff = r_combined["efficiency"]
    
    # Combined cycle should have ~11 percentage points higher efficiency
    efficiency_gain = combined_eff - simple_eff
    print(f"  Efficiency gain (CC vs simple): {efficiency_gain:.4f} "
          f"({efficiency_gain*100:.2f} pp, target ~11 pp)")
    assert efficiency_gain > 0.08, "Combined cycle should have significantly higher efficiency"


if __name__ == "__main__":
    tests = [
        test_package_data_bundled,
        test_scalar_dispatch_gt,
        test_scalar_dispatch_ccpp,
        test_array_dispatch,
        test_dispatch_profile,
        test_fleet_homogeneous,
        test_fleet_heterogeneous_mixed_types,
        test_rejects_bad_inputs,
        test_lm9000_simple_cycle_full_load,
        test_lm9000_part_load,
        test_lm9000_fleet_compatibility,
        test_lm9000_combined_cycle_full_load,
        test_lm9000_combined_cycle_part_load,
        test_lm9000_simple_vs_combined,
    ]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\nAll smoke tests passed.")
