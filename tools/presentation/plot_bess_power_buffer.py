from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import (
    DATA_DIR,
    bess_metrics,
    build_supercloud_trace,
    ensure_output_dirs,
    save_figure,
    set_plot_style,
    storage_duration_examples,
)


TAU_VALUES_S = [1.0, 2.0, 3.0, 5.0, 10.0, 30.0, 60.0]
TAU_PICK_S = 10.0


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    trace = build_supercloud_trace()
    t_sec = trace["t_sec"]
    p_mw = trace["p_mw"]

    rows = []
    metrics_by_tau = {}
    for tau_s in TAU_VALUES_S:
        metrics = bess_metrics(p_mw, t_sec, tau_s)
        metrics_by_tau[tau_s] = metrics
        rows.append({
            "tau_s": tau_s,
            "bess_power_mw": metrics["bess_power_mw"],
            "bess_energy_mwh": metrics["bess_energy_mwh"],
            "bess_energy_kwh": 1000.0 * metrics["bess_energy_mwh"],
            "equivalent_duration_s": metrics["equivalent_duration_s"],
            "equivalent_c_rate": metrics["equivalent_c_rate"],
            "gt_max_ramp_mw_s": metrics["gt_max_ramp_mw_s"],
        })

    sweep = pd.DataFrame(rows)
    sweep.to_csv(DATA_DIR / "bess_power_buffer_sweep.csv", index=False)

    picked = metrics_by_tau[TAU_PICK_S]
    e_swing_kwh = 1000.0 * (picked["e_batt_mwh"] - np.min(picked["e_batt_mwh"]))

    fig, axes = plt.subplots(3, 1, figsize=(12.5, 9.5), sharex=True)
    axes[0].plot(t_sec / 3600.0, p_mw, color="#2451A6", lw=0.9, label="AI facility load")
    axes[0].plot(t_sec / 3600.0, picked["p_gt_mw"], color="#1B998B", lw=1.3,
                 label=f"Gas turbine target, {TAU_PICK_S:.0f} s low-pass")
    axes[0].set_title("Power buffer: battery handles the fast mismatch")
    axes[0].set_ylabel("Power (MW)")
    axes[0].legend(loc="upper right")

    axes[1].plot(t_sec / 3600.0, picked["p_batt_mw"], color="#D1495B", lw=0.9)
    axes[1].axhline(0.0, color="#222222", ls="--", lw=1.0)
    axes[1].set_ylabel("Battery power (MW)")
    axes[1].set_title(f"Power rating is set by fast spikes: {picked['bess_power_mw']:.1f} MW")

    axes[2].plot(t_sec / 3600.0, e_swing_kwh, color="#F4A261", lw=1.1)
    axes[2].set_xlabel("Time in sampled window (h)")
    axes[2].set_ylabel("Battery energy swing (kWh)")
    axes[2].set_title(
        f"Energy capacity is small: {1000.0 * picked['bess_energy_mwh']:.1f} kWh "
        f"({picked['equivalent_duration_s']:.1f} s at rated power)"
    )

    out = save_figure(fig, "02_bess_power_buffer_timeseries.png")
    plt.close(fig)
    print(f"Wrote {out}")

    examples = storage_duration_examples()
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 8.6))

    axes[0, 0].plot(sweep["tau_s"], sweep["bess_power_mw"], "o-", color="#D1495B")
    axes[0, 0].set_xscale("log")
    axes[0, 0].set_title("Required battery MW")
    axes[0, 0].set_xlabel("Gas-turbine smoothing time constant (s)")
    axes[0, 0].set_ylabel("Power rating (MW)")

    axes[0, 1].plot(sweep["tau_s"], sweep["bess_energy_kwh"], "o-", color="#F4A261")
    axes[0, 1].set_xscale("log")
    axes[0, 1].set_title("Required battery energy")
    axes[0, 1].set_xlabel("Gas-turbine smoothing time constant (s)")
    axes[0, 1].set_ylabel("Energy swing (kWh)")

    axes[1, 0].scatter(sweep["bess_energy_kwh"], sweep["bess_power_mw"], s=82,
                       color="#1B998B", edgecolor="white", linewidth=0.8)
    for _, row in sweep.iterrows():
        axes[1, 0].annotate(f"{row['tau_s']:.0f}s", (row["bess_energy_kwh"], row["bess_power_mw"]),
                            xytext=(5, 4), textcoords="offset points", fontsize=9)
    axes[1, 0].set_title("AI smoothing lands in high-MW, low-MWh territory")
    axes[1, 0].set_xlabel("Energy swing (kWh)")
    axes[1, 0].set_ylabel("Power rating (MW)")

    labels = list(examples["label"]) + [f"AI buffer\n{TAU_PICK_S:.0f} s split"]
    c_rates = list(examples["c_rate"]) + [float(picked["equivalent_c_rate"])]
    colors = ["#A8DADC"] * len(examples) + ["#D1495B"]
    axes[1, 1].barh(labels, c_rates, color=colors)
    axes[1, 1].set_xscale("log")
    axes[1, 1].set_title("C-rate separates energy storage from power buffering")
    axes[1, 1].set_xlabel("C-rate = MW / MWh (log scale)")
    axes[1, 1].grid(axis="x", alpha=0.24)

    out = save_figure(fig, "03_bess_power_energy_sweep.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()