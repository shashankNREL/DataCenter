from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import DATA_DIR, build_supercloud_trace, ensure_output_dirs, save_figure, set_plot_style


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    trace = build_supercloud_trace()
    t_sec = trace["t_sec"]
    p_mw = trace["p_mw"]
    dpdt = trace["dpdt_mw_s"]

    i_event = int(np.argmax(np.abs(dpdt)))
    zoom_w = 30.0
    zoom_lo = max(0.0, float(t_sec[i_event]) - zoom_w / 2.0)
    zoom_hi = zoom_lo + zoom_w
    zoom_mask = (t_sec >= zoom_lo) & (t_sec <= zoom_hi)

    summary = pd.DataFrame([{
        "window_hours": float(t_sec[-1] / 3600.0),
        "min_mw": float(p_mw.min()),
        "mean_mw": float(p_mw.mean()),
        "peak_mw": float(p_mw.max()),
        "max_abs_dpdt_mw_s": float(np.abs(dpdt).max()),
        "p99_abs_dpdt_mw_s": float(np.percentile(np.abs(dpdt), 99)),
        "max_step_mw_per_100ms": float(np.diff(p_mw).max()),
        "min_step_mw_per_100ms": float(np.diff(p_mw).min()),
        "gpu_count_in_window": int(trace["gpu_count"]),
        "sample_count_in_window": int(trace["sample_count"]),
    }])
    summary.to_csv(DATA_DIR / "ai_workload_signature_summary.csv", index=False)

    fig, axes = plt.subplots(3, 1, figsize=(12.5, 9.2))

    axes[0].plot(t_sec / 3600.0, p_mw, color="#2451A6", lw=1.0)
    axes[0].set_title("AI workload power is not just a peak MW problem")
    axes[0].set_xlabel("Time in sampled window (h)")
    axes[0].set_ylabel("Facility load (MW)")
    axes[0].axhline(p_mw.mean(), color="#222222", ls="--", lw=1.0, label=f"Mean {p_mw.mean():.1f} MW")
    axes[0].axhline(p_mw.max(), color="#D1495B", ls=":", lw=1.2, label=f"Peak {p_mw.max():.1f} MW")
    axes[0].legend(loc="upper right")

    axes[1].plot(t_sec[zoom_mask], p_mw[zoom_mask], "o-", color="#1B998B", ms=3.2, lw=1.2)
    axes[1].axvline(t_sec[i_event], color="#D1495B", ls="--", lw=1.1,
                    label=f"Largest 100 ms ramp: {abs(dpdt[i_event]):.1f} MW/s")
    axes[1].set_title("The stressful events happen on sub-second time scales")
    axes[1].set_xlabel("Time around worst event (s)")
    axes[1].set_ylabel("Facility load (MW)")
    axes[1].legend(loc="best")

    axes[2].hist(np.abs(dpdt), bins=90, log=True, color="#F4A261", edgecolor="white", linewidth=0.3)
    p99 = float(np.percentile(np.abs(dpdt), 99))
    axes[2].axvline(p99, color="#D1495B", ls="--", lw=1.4, label=f"p99 = {p99:.1f} MW/s")
    axes[2].set_title("Ramp-rate distribution seen by the power plant")
    axes[2].set_xlabel("Absolute ramp rate, |dP/dt| (MW/s)")
    axes[2].set_ylabel("Count, log scale")
    axes[2].legend(loc="upper right")

    out = save_figure(fig, "01_ai_workload_signature.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()