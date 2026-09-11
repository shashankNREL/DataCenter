from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import DATA_DIR, ensure_output_dirs, save_figure, set_plot_style


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    fleet = pd.DataFrame([
        {"case": "1 GT", "installed_gas_mw": 22.0, "per_unit_avg_load_pct": 48.0,
         "fuel_multiplier": 1.00, "bess_power_mw": 0.0, "verdict": "Trips"},
        {"case": "2 GT", "installed_gas_mw": 44.0, "per_unit_avg_load_pct": 24.0,
         "fuel_multiplier": 1.34, "bess_power_mw": 0.0, "verdict": "Trips"},
        {"case": "3 GT", "installed_gas_mw": 66.0, "per_unit_avg_load_pct": 16.0,
         "fuel_multiplier": 1.68, "bess_power_mw": 0.0, "verdict": "Fails full trace"},
        {"case": "4 GT", "installed_gas_mw": 88.0, "per_unit_avg_load_pct": 12.0,
         "fuel_multiplier": 2.02, "bess_power_mw": 0.0, "verdict": "Passes"},
        {"case": "2 GT + BESS", "installed_gas_mw": 44.0, "per_unit_avg_load_pct": 24.0,
         "fuel_multiplier": 1.34, "bess_power_mw": 8.4, "verdict": "Passes"},
    ])
    fleet.to_csv(DATA_DIR / "fleet_tradeoff_metrics.csv", index=False)

    status_colors = {
        "Trips": "#D1495B",
        "Fails full trace": "#F4A261",
        "Passes": "#1B998B",
    }
    colors = [status_colors[v] for v in fleet["verdict"]]

    fig, axes = plt.subplots(2, 2, figsize=(12.8, 9.4))

    axes[0, 0].bar(fleet["case"], fleet["installed_gas_mw"], color=colors)
    axes[0, 0].axhline(18.0, color="#222222", ls="--", lw=1.1, label="18 MW AI load peak")
    axes[0, 0].set_title("Gas capacity alone does not guarantee ride-through")
    axes[0, 0].set_ylabel("Installed gas capacity (MW)")
    axes[0, 0].tick_params(axis="x", rotation=20)
    axes[0, 0].legend(loc="upper left")

    axes[0, 1].bar(fleet["case"], fleet["fuel_multiplier"], color=colors)
    axes[0, 1].axhline(1.0, color="#222222", ls="--", lw=1.0)
    axes[0, 1].set_title("Overbuilding turbines buys dynamics with fuel")
    axes[0, 1].set_ylabel("Fuel burn relative to 1 GT energy case")
    axes[0, 1].tick_params(axis="x", rotation=20)

    axes[1, 0].bar(fleet["case"], fleet["per_unit_avg_load_pct"], color=colors)
    axes[1, 0].axhspan(0, 20, color="#D1495B", alpha=0.11, label="Very low-load region")
    axes[1, 0].set_title("Low-load operation is the hidden fleet penalty")
    axes[1, 0].set_ylabel("Average load per running turbine (%)")
    axes[1, 0].tick_params(axis="x", rotation=20)
    axes[1, 0].legend(loc="upper right")

    axes[1, 1].scatter(fleet["installed_gas_mw"], fleet["bess_power_mw"],
                       s=260 * fleet["fuel_multiplier"], color=colors,
                       edgecolor="white", linewidth=1.2)
    for _, row in fleet.iterrows():
        axes[1, 1].annotate(row["case"], (row["installed_gas_mw"], row["bess_power_mw"]),
                            xytext=(7, 5), textcoords="offset points", fontsize=9)
    axes[1, 1].set_title("Fast buffering can replace idling gas capacity")
    axes[1, 1].set_xlabel("Installed gas capacity (MW)")
    axes[1, 1].set_ylabel("Fast buffer power (MW)")

    out = save_figure(fig, "05_fleet_tradeoffs.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()