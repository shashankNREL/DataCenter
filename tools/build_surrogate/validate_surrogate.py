"""Round-trip validation: GasTurbinePlant + Fleet vs. the Modelica reference.

At the original sweep nodes the Python interpolator must reproduce the
Modelica outputs exactly. Between nodes it must be smooth. Plots are
saved under tools/build_surrogate/validation/.

Run with the `datacenter` conda env:
    ~/miniconda3/envs/datacenter/bin/python tools/build_surrogate/validate_surrogate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Allow `import gas_plant` when running from project root or this folder.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gas_plant import GasTurbinePlant, Fleet  # noqa: E402

REFERENCE_CSV = ROOT / "data" / "gas_turbine_surrogate.csv"
PLOT_OUT = ROOT / "tools" / "build_surrogate" / "validation" / "surrogate_vs_modelica.png"


def check_nodes_match():
    """At each sweep node, Python output must equal Modelica output to FP."""
    ref = pd.read_csv(REFERENCE_CSV)
    plant = GasTurbinePlant()  # defaults: 235 MW, NG, LHV 49e6
    out = plant.dispatch(ref["GTLoad"].to_numpy())

    diffs = {
        "P_el_W": np.max(np.abs(out["power_w"] - ref["P_el_W"])),
        "fuel_kg_s": np.max(np.abs(out["fuel_kg_s"] - ref["fuelFlowRate_kg_s"])),
        "exhaust_m_kg_s": np.max(
            np.abs(out["exhaust_m_kg_s"] - ref["exhaust_m_flow_kg_s"])
        ),
        "exhaust_T_K": np.max(np.abs(out["exhaust_T_K"] - ref["exhaust_T_K"])),
    }
    print("Max abs diff at sweep nodes (must be ~0):")
    for k, v in diffs.items():
        print(f"  {k:15s} = {v:.3e}")
    worst = max(diffs.values())
    if worst > 1e-6:
        raise SystemExit(f"FAIL: node-match diff {worst:.3e} exceeds tolerance")
    print("PASS: surrogate reproduces Modelica outputs at sweep nodes.")


def check_dispatch_scalar_and_profile():
    plant = GasTurbinePlant()
    s = plant.dispatch(0.5)
    assert isinstance(s["power_w"], float)
    print(f"\nScalar dispatch @ GTLoad=0.5: {s['power_w']/1e6:.2f} MW, "
          f"{s['fuel_kg_s']:.3f} kg/s fuel, eff={s['efficiency']:.3f}, "
          f"CO2={s['co2_kg_s']:.3f} kg/s")

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    profile = pd.Series(np.linspace(0.3, 1.0, 24), index=idx)
    df = plant.dispatch_profile(profile)
    assert df.shape == (24, 6)
    print(f"Profile dispatch (24 h ramp): peak={df['power_w'].max()/1e6:.1f} MW, "
          f"fleet total fuel={df['fuel_kg_s'].sum()*3600:.0f} kg over 24h")


def check_fleet():
    fleet = Fleet([GasTurbinePlant() for _ in range(3)])
    print(f"\nFleet repr: {fleet}")
    out = fleet.dispatch(0.8)
    print(f"Fleet @ 0.8 (3 x 235 MW units): P={out['power_w']/1e6:.1f} MW, "
          f"fuel={out['fuel_kg_s']:.2f} kg/s, eff={out['efficiency']:.3f}, "
          f"mixed exhaust T={out['exhaust_T_K_mixed']:.0f} K")
    # Per-unit loads (heterogeneous dispatch)
    out_het = fleet.dispatch(np.array([1.0, 0.6, 0.3]))
    print(f"Fleet hetero [1.0, 0.6, 0.3]: P={out_het['power_w']/1e6:.1f} MW, "
          f"fuel={out_het['fuel_kg_s']:.2f} kg/s, "
          f"per-unit P={[r['power_w']/1e6 for r in out_het['units']]}")

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    profile = pd.Series(np.linspace(0.4, 0.95, 24), index=idx)
    df = fleet.dispatch_profile(profile)
    print(f"Fleet profile: peak={df['power_w'].max()/1e6:.0f} MW, "
          f"day-avg eff={df['efficiency'].mean():.3f}")


def check_rated_power_scaling():
    """A 100 MW unit should produce ~100/235 of the 235 MW unit's outputs."""
    big = GasTurbinePlant(rated_power_mw=235)
    small = GasTurbinePlant(rated_power_mw=100)
    b = big.dispatch(0.8)
    s = small.dispatch(0.8)
    ratio = s["power_w"] / b["power_w"]
    expected = 100 / 235
    print(f"\nRated-power scaling check @ load=0.8: "
          f"small/big power ratio = {ratio:.4f}, expected = {expected:.4f}")
    assert abs(ratio - expected) < 1e-9
    # Exhaust T should be unchanged (thermodynamic, not size-scaled)
    assert abs(s["exhaust_T_K"] - b["exhaust_T_K"]) < 1e-9
    print("PASS: power/fuel/exhaust-flow scale linearly with rated_power_mw; "
          "exhaust temperature is size-invariant.")


def plot_surrogate_vs_modelica():
    ref = pd.read_csv(REFERENCE_CSV)
    plant = GasTurbinePlant()

    # Fine grid for the interpolator
    fine = np.linspace(0, 1, 401)
    out = plant.dispatch(fine)

    fig, axes = plt.subplots(2, 3, figsize=(13, 7), sharex=True)
    axes = axes.flatten()

    plots = [
        ("power_w", "P_el_W", "Electrical power [MW]", 1e-6),
        ("fuel_kg_s", "fuelFlowRate_kg_s", "Fuel flow [kg/s]", 1.0),
        ("exhaust_m_kg_s", "exhaust_m_flow_kg_s", "Exhaust mass flow [kg/s]", 1.0),
        ("exhaust_T_K", "exhaust_T_K", "Exhaust temperature [K]", 1.0),
        ("efficiency", None, "Thermal efficiency [-]", 1.0),
        ("co2_kg_s", None, "CO2 emissions [kg/s]", 1.0),
    ]
    for ax, (py_key, csv_col, title, scale) in zip(axes, plots):
        ax.plot(fine, out[py_key] * scale, "-", lw=1.5, label="Python surrogate")
        if csv_col:
            ax.plot(
                ref["GTLoad"], ref[csv_col] * scale, "o", ms=5,
                color="C3", label="Modelica nodes",
            )
        ax.set_title(title)
        ax.set_xlabel("GTLoad [-]")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        "Simple-cycle GT surrogate: Python interp vs. ThermoPower (235 MW default)",
        fontsize=11,
    )
    fig.tight_layout()
    PLOT_OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOT_OUT, dpi=120)
    print(f"\nWrote {PLOT_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    check_nodes_match()
    check_dispatch_scalar_and_profile()
    check_fleet()
    check_rated_power_scaling()
    plot_surrogate_vs_modelica()
