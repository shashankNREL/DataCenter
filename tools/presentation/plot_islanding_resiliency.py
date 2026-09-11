from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import DATA_DIR, ensure_output_dirs, save_figure, set_plot_style


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    metrics = pd.DataFrame([
        {"metric": "Stable islanding", "value": 0.037, "limit": 1.0, "unit": "Hz excursion"},
        {"metric": "Naive resync", "value": 8.58, "limit": 1.0, "unit": "Hz excursion"},
        {"metric": "DC bus voltage", "value": 0.994, "limit": 0.95, "unit": "pu voltage"},
        {"metric": "Fuel reserve", "value": 2.3, "limit": 2.0, "unit": "hours on 90 t LNG"},
        {"metric": "CO2 rate", "value": 107.0, "limit": None, "unit": "t/h at 200 MW"},
    ])
    metrics.to_csv(DATA_DIR / "islanding_resiliency_metrics.csv", index=False)

    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.0))

    axes[0, 0].bar(["Prepared island", "Naive resync"], [0.037, 8.58],
                   color=["#1B998B", "#D1495B"])
    axes[0, 0].axhline(1.0, color="#222222", ls="--", lw=1.0, label="Example +/-1 Hz tolerance")
    axes[0, 0].set_yscale("log")
    axes[0, 0].set_ylabel("Maximum frequency excursion (Hz, log scale)")
    axes[0, 0].set_title("Islanding and resync are different control problems")
    axes[0, 0].legend(loc="upper left")

    axes[0, 1].bar(["Frequency margin", "Voltage margin"],
                   [1.0 - 0.037, 0.994 - 0.95], color=["#2451A6", "#1B998B"])
    axes[0, 1].set_title("A prepared island can keep IT-side power quality")
    axes[0, 1].set_ylabel("Margin to illustrative limit")

    axes[1, 0].bar(["Fuel reserve"], [2.3], color="#F4A261")
    axes[1, 0].axhline(2.0, color="#222222", ls="--", lw=1.0, label="2 h reference")
    axes[1, 0].set_ylim(0, 3.0)
    axes[1, 0].set_title("Reliability becomes a fuel-logistics question")
    axes[1, 0].set_ylabel("Hours at 200 MW on 90 t LNG")
    axes[1, 0].legend(loc="upper right")

    axes[1, 1].bar(["CO2 during outage"], [107.0], color="#6C757D")
    axes[1, 1].set_title("Behind-the-meter resilience has an emissions shadow")
    axes[1, 1].set_ylabel("CO2 rate (t/h at 200 MW)")

    out = save_figure(fig, "06_islanding_resiliency_summary.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()