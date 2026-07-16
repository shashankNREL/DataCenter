"""Example 2: data-center resiliency under extended grid outage.

Addresses §5 of `andes_coupling_benefits.md`:
    "If the grid drops for X hours, can on-site gas + UPS carry the
     data-center load without violating any IT-side reliability target?"

Setup:
    Bus(Gas) --- L_LOCAL --- Bus(DataCenter) --- L_TIE --- Bus(Grid)
        |                          |                          |
     235 MW GT                  200 MW load             infinite bus

Scenario:
    t=0-2s    Grid-tied, gas at ~85 % load.
    t=2s      Grid trips. Sustained outage begins.
    t=2-60s   Islanded operation. Gas alone supplies the data center.
    t=60s     End of simulation. (Steady state reached well before this;
              the 58 s islanded window confirms long-term survival.)

The simulation extracts FOUR resiliency metrics that a data-center
operator / planner cares about. The coupled tool gives all four in one
run — neither ANDES alone nor gas_plant alone can.

  1. Frequency band:   peak excursion (must stay within IT-acceptable
                       band, typically ±1 Hz for tier-IV data centers).
  2. Voltage band:     DC bus voltage (must be > 0.95 pu for normal
                       operation; ride-through standards allow lower
                       briefly).
  3. Fuel reserve:     cumulative fuel burned over the outage; given
                       on-site storage tankage, you can compute hours
                       of fuel reserve.
  4. CO2 footprint:    cumulative CO2 emitted; matters for sustainability
                       reporting and corporate emissions caps.

Run:
    ~/miniconda3/envs/datacenter/bin/python examples/example_resiliency.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gas_plant import GasTurbinePlant
from gas_plant_andes import IslandedCaseConfig, run_islanding_scenario


# Operator-defined reliability bands — these are policy inputs the larger
# tool would supply. Defaults chosen to match common data-center practice.
FREQ_BAND_HZ = 1.0     # ± 1 Hz around 60 Hz
VOLTAGE_FLOOR_PU = 0.95
# On-site fuel tankage in kg of natural gas. A typical 200 m3 LNG tank
# stores ~90,000 kg. Choose a small number here to make the reserve
# calculation interesting.
ON_SITE_FUEL_RESERVE_KG = 90_000.0


def evaluate_resiliency(result, summary):
    """Compute and print the four resiliency metrics."""

    nominal = result.nominal_freq_hz
    peak_excursion = summary["max_excursion_hz"]
    freq_ok = peak_excursion <= FREQ_BAND_HZ

    min_voltage = float(result.voltage_dc_pu.min())
    voltage_ok = min_voltage >= VOLTAGE_FLOOR_PU

    total_fuel = summary["total_fuel_kg"]
    total_co2 = summary["total_co2_kg"]
    duration = summary["duration_s"]

    # Steady-state fuel burn rate after the transient settles
    settled_mask = result.t >= max(15.0, result.t[-1] / 2)
    steady_fuel_rate_kg_s = float(np.mean(result.fuel_kg_s[settled_mask]))
    hours_of_reserve = ON_SITE_FUEL_RESERVE_KG / steady_fuel_rate_kg_s / 3600.0

    print("\n=== Resiliency metrics ===\n")
    print(f"  1. Frequency band [±{FREQ_BAND_HZ:.2f} Hz]:")
    print(f"     peak excursion = {peak_excursion:.3f} Hz   "
          f"=> {'PASS' if freq_ok else 'FAIL'}")
    print(f"  2. Voltage floor [≥ {VOLTAGE_FLOOR_PU:.2f} pu]:")
    print(f"     min DC voltage = {min_voltage:.4f} pu     "
          f"=> {'PASS' if voltage_ok else 'FAIL'}")
    print(f"  3. Fuel use over the {duration:.0f}-second outage:")
    print(f"     {total_fuel:.1f} kg burned")
    print(f"     steady-state burn rate = {steady_fuel_rate_kg_s:.3f} kg/s")
    print(f"     => {hours_of_reserve:.1f} hours of on-site reserve "
          f"({ON_SITE_FUEL_RESERVE_KG/1e3:.0f} t LNG tank assumption)")
    print(f"  4. CO2 emitted during outage: {total_co2:.1f} kg "
          f"(={total_co2/1000:.2f} t over {duration:.0f} s)")

    return {
        "freq_pass": freq_ok,
        "voltage_pass": voltage_ok,
        "hours_of_reserve": hours_of_reserve,
        "steady_fuel_rate_kg_s": steady_fuel_rate_kg_s,
    }


def main():
    plant = GasTurbinePlant()

    cfg = IslandedCaseConfig(
        plant=plant,
        plant_load_setpoint=0.85106,    # ≈ 200 MW, matching the DC load
        data_center_mw=200.0,
        data_center_mvar=0.0,
        island_time_s=2.0,
        resync_time_s=None,             # sustained outage; never reclose
    )

    print(f"Plant: {plant} (rated {plant.rated_power_mw} MW)")
    print(f"Pre-island P_gas = {cfg.plant_p_mw:.2f} MW, P_load = {cfg.data_center_mw} MW")
    print(f"Outage duration  = {60 - cfg.island_time_s:.0f} s of islanding\n")

    result = run_islanding_scenario(cfg, duration_s=60.0, tstep_s=0.01)
    s = result.summary()

    print("Raw scenario summary:")
    for k, v in s.items():
        print(f"  {k:25s} = {v:.4f}")

    metrics = evaluate_resiliency(result, s)

    # ---- plots
    out_path = ROOT / "examples" / "output" / "resiliency_extended_outage.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True)
    ax_f, ax_v, ax_p, ax_e = axes.flatten()

    # Frequency
    ax_f.plot(result.t, result.freq_gas_hz, color="C0", lw=1.5)
    ax_f.axhline(60.0, color="gray", ls=":", lw=1, label="Nominal 60 Hz")
    ax_f.axhspan(60.0 - FREQ_BAND_HZ, 60.0 + FREQ_BAND_HZ,
                 color="green", alpha=0.10, label=f"±{FREQ_BAND_HZ:.1f} Hz band")
    ax_f.axvspan(cfg.island_time_s, result.t[-1], color="orange", alpha=0.10, label="Islanded")
    ax_f.set_ylabel("Frequency [Hz]")
    ax_f.set_title("Frequency during sustained grid outage")
    ax_f.legend(loc="best", fontsize=9)
    ax_f.grid(True, alpha=0.3)

    # Voltage
    ax_v.plot(result.t, result.voltage_dc_pu, color="C1", lw=1.5)
    ax_v.axhline(VOLTAGE_FLOOR_PU, color="C3", ls="--", lw=1,
                 label=f"Voltage floor ({VOLTAGE_FLOOR_PU:.2f} pu)")
    ax_v.axhline(1.0, color="gray", ls=":", lw=1)
    ax_v.axvspan(cfg.island_time_s, result.t[-1], color="orange", alpha=0.10)
    ax_v.set_ylabel("Bus voltage [pu]")
    ax_v.set_title("Data-center bus voltage")
    ax_v.legend(loc="best", fontsize=9)
    ax_v.grid(True, alpha=0.3)
    ax_v.set_ylim(0.9, 1.02)

    # Plant power
    ax_p.plot(result.t, result.plant_power_mw, color="C2", lw=1.5, label="Gas Pm")
    ax_p.axhline(cfg.data_center_mw, color="C3", ls="--", lw=1,
                 label=f"DC load ({cfg.data_center_mw:.0f} MW)")
    ax_p.axhline(plant.rated_power_mw, color="C7", ls=":", lw=1,
                 label=f"Rated ({plant.rated_power_mw:.0f} MW)")
    ax_p.axvspan(cfg.island_time_s, result.t[-1], color="orange", alpha=0.10)
    ax_p.set_ylabel("Gas plant Pm [MW]")
    ax_p.set_xlabel("Time [s]")
    ax_p.set_title("Gas plant output (capacity headroom)")
    ax_p.legend(loc="best", fontsize=9)
    ax_p.grid(True, alpha=0.3)

    # Cumulative fuel + CO2
    ax_e.plot(result.t, result.cumulative_fuel_kg, color="C5", lw=1.5, label="Cumulative fuel")
    ax_e2 = ax_e.twinx()
    ax_e2.plot(result.t, result.cumulative_co2_kg, color="C6", lw=1.5, ls="--", label="Cumulative CO2")
    ax_e.set_ylabel("Fuel [kg]", color="C5")
    ax_e.tick_params(axis="y", labelcolor="C5")
    ax_e2.set_ylabel("CO2 [kg]", color="C6")
    ax_e2.tick_params(axis="y", labelcolor="C6")
    ax_e.set_xlabel("Time [s]")
    ax_e.axvspan(cfg.island_time_s, result.t[-1], color="orange", alpha=0.10)
    ax_e.set_title("Cumulative fuel + CO2 over outage")
    h1, l1 = ax_e.get_legend_handles_labels()
    h2, l2 = ax_e2.get_legend_handles_labels()
    ax_e.legend(h1 + h2, l1 + l2, loc="best", fontsize=9)
    ax_e.grid(True, alpha=0.3)

    fig.suptitle(
        f"Extended grid outage (§5 resiliency study)\n"
        f"Headroom: gas at ≈{cfg.plant_p_mw:.0f} MW vs. {plant.rated_power_mw:.0f} MW rated  •  "
        f"Reserve: {metrics['hours_of_reserve']:.1f} h on {ON_SITE_FUEL_RESERVE_KG/1e3:.0f} t fuel  •  "
        f"Δf max: {s['max_excursion_hz']:.3f} Hz",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=120)
    print(f"\nPlot written to {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
