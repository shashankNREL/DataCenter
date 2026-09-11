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

    rows = [
        {"architecture": "Grid only", "Peak capacity": 4, "Fast ramps": 2, "Islanded ride-through": 1,
         "Fuel/CO2 performance": 4, "Operational simplicity": 4, "Grid friendliness": 2},
        {"architecture": "Single GT", "Peak capacity": 3, "Fast ramps": 1, "Islanded ride-through": 2,
         "Fuel/CO2 performance": 2, "Operational simplicity": 3, "Grid friendliness": 2},
        {"architecture": "Single GT + BESS", "Peak capacity": 4, "Fast ramps": 4, "Islanded ride-through": 3,
         "Fuel/CO2 performance": 3, "Operational simplicity": 2, "Grid friendliness": 4},
        {"architecture": "2 GT + BESS", "Peak capacity": 5, "Fast ramps": 4, "Islanded ride-through": 4,
         "Fuel/CO2 performance": 3, "Operational simplicity": 2, "Grid friendliness": 4},
        {"architecture": "4 GT only", "Peak capacity": 5, "Fast ramps": 4, "Islanded ride-through": 4,
         "Fuel/CO2 performance": 1, "Operational simplicity": 3, "Grid friendliness": 3},
        {"architecture": "Grid + GT + BESS + workload API", "Peak capacity": 5, "Fast ramps": 5,
         "Islanded ride-through": 5, "Fuel/CO2 performance": 4, "Operational simplicity": 1,
         "Grid friendliness": 5},
    ]
    scores = pd.DataFrame(rows).set_index("architecture")
    scores.to_csv(DATA_DIR / "architecture_tradeoff_scores.csv")

    fig, ax = plt.subplots(figsize=(12.5, 6.7))
    im = ax.imshow(scores.to_numpy(), vmin=1, vmax=5, cmap="YlGnBu")

    ax.set_xticks(np.arange(scores.shape[1]), labels=scores.columns)
    ax.set_yticks(np.arange(scores.shape[0]), labels=scores.index)
    ax.tick_params(axis="x", rotation=25)
    ax.set_title("Behind-the-meter architecture tradeoff map")

    for i in range(scores.shape[0]):
        for j in range(scores.shape[1]):
            ax.text(j, i, int(scores.iloc[i, j]), ha="center", va="center",
                    color="white" if scores.iloc[i, j] >= 4 else "#222222",
                    fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, shrink=0.82)
    cbar.set_label("Score: 1 = weak, 5 = strong")
    ax.text(
        0.0,
        -0.18,
        "Scores are presentation prompts, not optimized design outputs. They mark where the debate should happen.",
        transform=ax.transAxes,
        fontsize=10,
        color="#555555",
    )

    out = save_figure(fig, "07_architecture_tradeoff_map.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()