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
            "f_min_hz": 20.730,
            "f_max_hz": 71.093,
            "trip": "Trips",
            "note": "Fast AI ramps exceed governor and inertia limits",
        },
        {
            "case": "2 GT\nno buffer",
            "f_min_hz": 27.961,
            "f_max_hz": 69.387,
            "trip": "Trips",
            "note": "Crosses the protection threshold during the full trace",
        },
        {
            "case": "3 GT\nno buffer",
            "f_min_hz": 20.000,
            "f_max_hz": 1384.826,
            "trip": "Trips",
            "note": "Trips later in the full trace; post-trip values are not physical",
        },
        {
            "case": "4 GT fleet\nno buffer",
            "f_min_hz": 59.527,
            "f_max_hz": 60.675,
            "trip": "Rides through",
            "note": "More spinning machines add inertia and ramp capability",
        },
        {
            "case": "Single GT\n+ BESS",
            "f_min_hz": 59.800,
            "f_max_hz": 60.320,
            "trip": "Rides through",
            "note": "Fast power mismatch is buffered before the turbine sees it",
        },
    ])
    cases.to_csv(DATA_DIR / "frequency_trip_story.csv", index=False)

    colors = ["#D1495B" if outcome == "Trips" else "#1B998B" for outcome in cases["trip"]]
    fig, ax = plt.subplots(figsize=(13.8, 6.2))

    bars = ax.bar(cases["case"], cases["f_min_hz"], color=colors, alpha=0.86)
    ax.axhspan(59.4, 60.6, color="#1B998B", alpha=0.12, label="Continuous operation band: 59.4-60.6 Hz")
    ax.axhline(57.8, color="#D1495B", lw=1.4, ls="--", label="Trip territory: below 57.8 Hz")
    ax.axhline(60.0, color="#222222", lw=1.0, ls=":", label="Nominal 60 Hz")

    for bar, (_, row) in zip(bars, cases.iterrows()):
        x = bar.get_x() + bar.get_width() / 2.0
        y = row["f_min_hz"]
        ax.text(x, y + 0.7, f"min {y:.1f} Hz", ha="center", va="bottom",
                fontsize=12, fontweight="bold", color="#222222")
        outcome_y = max(19.3, y - 2.0) if row["trip"] == "Trips" else 25.0
        ax.text(x, outcome_y, row["trip"], ha="center", va="center", fontsize=13,
            color="white", fontweight="bold")

    ax.set_ylabel("Minimum generator frequency (Hz)")
    ax.set_title("Enough megawatts is not the same as ride-through")
    ax.set_ylim(18.0, 63.0)
    ax.legend(loc="upper left")

    ax.text(
        0.02,
        -0.20,
        "Same 2 h AI trace for every case. Red cases cross 57.8 Hz; their later minima are model extrapolations,\n"
        "because real protection would disconnect the generator at the first trip crossing.",
        transform=ax.transAxes,
        fontsize=10,
        color="#555555",
    )

    out = save_figure(fig, "08_frequency_trip_story.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()