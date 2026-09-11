from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import DATA_DIR, ensure_output_dirs, save_figure, set_plot_style


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    cases = pd.DataFrame([
        {
            "case": "Single GT\nno buffer",
            "f_min_hz": 20.7,
            "f_max_hz": 71.1,
            "trip": "Trips",
            "note": "Fast AI ramps exceed governor and inertia limits",
        },
        {
            "case": "Single GT\n+ BESS",
            "f_min_hz": 59.8,
            "f_max_hz": 60.32,
            "trip": "Rides through",
            "note": "Fast power mismatch is buffered before the turbine sees it",
        },
        {
            "case": "4 GT fleet\nno buffer",
            "f_min_hz": 59.53,
            "f_max_hz": 60.68,
            "trip": "Rides through",
            "note": "More spinning machines add inertia and ramp capability",
        },
    ])
    cases.to_csv(DATA_DIR / "frequency_trip_story.csv", index=False)

    colors = ["#D1495B", "#1B998B", "#1B998B"]
    fig, ax = plt.subplots(figsize=(12.5, 5.8))

    bars = ax.bar(cases["case"], cases["f_min_hz"], color=colors, alpha=0.86)
    ax.axhspan(59.4, 60.6, color="#1B998B", alpha=0.12, label="Continuous operation band: 59.4-60.6 Hz")
    ax.axhline(57.8, color="#D1495B", lw=1.4, ls="--", label="Trip territory: below 57.8 Hz")
    ax.axhline(60.0, color="#222222", lw=1.0, ls=":", label="Nominal 60 Hz")

    for bar, (_, row) in zip(bars, cases.iterrows()):
        x = bar.get_x() + bar.get_width() / 2.0
        y = row["f_min_hz"]
        ax.text(x, y + 1.0, f"min {y:.1f} Hz", ha="center", va="bottom",
                fontsize=12, fontweight="bold", color="#222222")
        outcome_y = y - 2.0 if row["trip"] == "Trips" else 25.0
        ax.text(x, outcome_y, row["trip"], ha="center", va="center", fontsize=13,
            color="white", fontweight="bold")

    ax.set_ylabel("Minimum generator frequency (Hz)")
    ax.set_title("Enough megawatts is not the same as ride-through")
    ax.set_ylim(18.0, 63.0)
    ax.legend(loc="upper left")

    ax.text(
        0.02,
        -0.20,
        "Ranges come from the corrected LM2500 AI-workload notebook and fleet-study review outputs.",
        transform=ax.transAxes,
        fontsize=10,
        color="#555555",
    )

    out = save_figure(fig, "08_frequency_trip_story.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()