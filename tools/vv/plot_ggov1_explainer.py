"""Generate novice-oriented GGOV1 explainer plots.

Run:
  pixi run python tools/vv/plot_ggov1_explainer.py
  pixi run python tools/vv/plot_ggov1_explainer.py --export-csv

Outputs:
  docs/figs/ggov1_01_load_step_story.png
  docs/figs/ggov1_02_controller_arbitration.png
  docs/figs/ggov1_03_droop_vs_isochronous.png
  docs/figs/ggov1_04_thermal_limiter_takeover.png
  docs/figs/ggov1_05_acceleration_limiter.png
  docs/figs/ggov1_06_validation_context.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gas_plant.dynamics.ggov1 import GGOV1Params, GGOV1Result, step_response  # noqa: E402
from validate_hannett import part_a as hannett_part_a  # noqa: E402


DEFAULT_OUTDIR = ROOT / "docs" / "figs"
DEFAULT_STEP_TIME_S = 5.0

ANNOTATION_BOX = {
    "boxstyle": "round,pad=0.35",
    "facecolor": "#fffbea",
    "edgecolor": "0.35",
    "linewidth": 0.8,
    "alpha": 0.96,
}

FOCUS_BOX = {
    "boxstyle": "round,pad=0.35",
    "facecolor": "white",
    "edgecolor": "0.65",
    "linewidth": 0.8,
    "alpha": 0.94,
}


def rel_time(result: GGOV1Result, t_step_s: float = DEFAULT_STEP_TIME_S) -> np.ndarray:
    return result.t_s - t_step_s


def setup_axis(ax: plt.Axes, xlabel: str, ylabel: str) -> None:
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    ax.axvline(0.0, color="0.35", linestyle=":", linewidth=1.0, label="Load step")


def finish(fig: plt.Figure, out_path: Path, dpi: int) -> Path:
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return out_path


def nearest_index(x: np.ndarray, target: float) -> int:
    return int(np.argmin(np.abs(x - target)))


def first_sustained_index(mask: np.ndarray, samples: int = 10) -> int | None:
    """Return the first index at which `mask` remains true for `samples`."""
    if mask.size < samples:
        return None
    run = np.convolve(mask.astype(int), np.ones(samples, dtype=int), mode="valid")
    hits = np.flatnonzero(run == samples)
    return int(hits[0]) if hits.size else None


def first_true_segment(mask: np.ndarray, minimum_samples: int = 5) -> tuple[int, int] | None:
    """Return the first contiguous true segment with sufficient duration."""
    padded = np.r_[False, mask, False]
    changes = np.diff(padded.astype(int))
    starts = np.flatnonzero(changes == 1)
    stops = np.flatnonzero(changes == -1)
    for start, stop in zip(starts, stops):
        if stop - start >= minimum_samples:
            return int(start), int(stop - 1)
    return None


def callout(
    ax: plt.Axes,
    text: str,
    xy: tuple[float, float],
    xytext: tuple[float, float],
    color: str = "0.2",
    align: str = "left",
) -> None:
    ax.annotate(
        text,
        xy=xy,
        xytext=xytext,
        textcoords="data",
        ha=align,
        va="center",
        fontsize=8,
        color=color,
        bbox=ANNOTATION_BOX,
        arrowprops={
            "arrowstyle": "->",
            "color": color,
            "linewidth": 1.0,
            "connectionstyle": "arc3,rad=0.08",
        },
        zorder=10,
    )


def focus_note(
    ax: plt.Axes,
    text: str,
    xy_axes: tuple[float, float] = (0.98, 0.05),
    align: str = "right",
    vertical: str = "bottom",
) -> None:
    ax.text(
        xy_axes[0],
        xy_axes[1],
        text,
        transform=ax.transAxes,
        ha=align,
        va=vertical,
        fontsize=8,
        bbox=FOCUS_BOX,
        zorder=9,
    )


def export_response_csv(
    name: str,
    result: GGOV1Result,
    csv_dir: Path,
    t_step_s: float = DEFAULT_STEP_TIME_S,
    **derived: np.ndarray,
) -> Path:
    csv_dir.mkdir(parents=True, exist_ok=True)
    df = result.as_dataframe()
    df.insert(1, "t_after_step_s", result.t_s - t_step_s)
    for column, values in derived.items():
        df[column] = values
    out_path = csv_dir / f"{name}.csv"
    df.to_csv(out_path, index=False)
    return out_path


def selected_branch(result: GGOV1Result) -> np.ndarray:
    branches = np.vstack([result.fsrn_pu, result.fsra_pu, result.fsrt_pu])
    return np.argmin(branches, axis=0)


def plot_load_step_story(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    params = GGOV1Params.lm2500_overrides()
    result = step_response(
        11.5, 14.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=80.0,
        sample_dt_s=0.02,
        params=params,
    )
    t = rel_time(result)

    fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.5), sharex=True)
    axes[0].step(t, result.Pe_demand_mw, where="post", color="k", linestyle="--",
                 label="Electrical load demand")
    axes[0].plot(t, result.Pe_mw, color="tab:orange", label="Actual electrical load")
    axes[0].plot(t, result.Pm_mw, color="tab:blue", label="Mechanical turbine power")
    setup_axis(axes[0], "Time after load step (s)", "Power (MW)")
    axes[0].set_title("GGOV1 load-step response: power balance")
    axes[0].legend(fontsize=8)

    i_early = nearest_index(t, 0.15)
    i_catchup = nearest_index(t, 3.0)
    callout(
        axes[0],
        "1  Load rises almost instantly.\nElectrical demand initially exceeds\nturbine mechanical power.",
        (t[i_early], result.Pe_demand_mw[i_early]),
        (0.75, result.Pe_demand_mw[i_early] + 0.75),
    )
    callout(
        axes[0],
        "3  The governor adds fuel;\nmechanical power catches up.",
        (t[i_catchup], result.Pm_mw[i_catchup]),
        (5.1, result.Pm_mw[i_catchup] - 0.9),
        color="tab:blue",
    )

    axes[1].plot(t, result.freq_hz, color="tab:green", label="Generator frequency")
    axes[1].axhline(60.0, color="k", linestyle=":", linewidth=1.0, label="Nominal 60 Hz")
    setup_axis(axes[1], "Time after load step (s)", "Frequency (Hz)")
    axes[1].set_title("Frequency is the symptom of power imbalance")
    axes[1].legend(fontsize=8)
    axes[1].set_xlim(0.0, 10.0)

    post_step = t >= 0.0
    i_nadir_local = int(np.argmin(result.freq_hz[post_step]))
    i_nadir = int(np.flatnonzero(post_step)[i_nadir_local])
    callout(
        axes[1],
        "2  Frequency falls while the rotor\nsupplies the missing power.\nThe nadir is the lowest frequency.",
        (t[i_nadir], result.freq_hz[i_nadir]),
        (min(t[i_nadir] + 2.0, 7.0), result.freq_hz[i_nadir] + 0.055),
        color="tab:green",
    )
    focus_note(
        axes[1],
        "Focus on the sequence:\npower mismatch → frequency dip → governor recovery",
    )

    outputs = [finish(fig, outdir / "ggov1_01_load_step_story.png", dpi)]
    if export_csv:
        outputs.append(export_response_csv("ggov1_01_load_step_story", result, outdir / "ggov1_explainer_signals"))
    return outputs


def plot_controller_arbitration(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    params = GGOV1Params.lm2500_overrides()
    result = step_response(
        11.5, 14.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=80.0,
        sample_dt_s=0.02,
        params=params,
    )
    active = selected_branch(result)
    t = rel_time(result)

    fig, axes = plt.subplots(2, 1, figsize=(9.5, 7.0), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(t, result.fsrn_pu, color="tab:blue", label="FSRN: speed/power request")
    axes[0].plot(t, result.fsra_pu, color="tab:red", label="FSRA: acceleration limiter request")
    axes[0].plot(t, result.fsrt_pu, color="tab:purple", label="FSRT: temperature/load limiter request")
    axes[0].plot(t, result.fsr_pu, color="k", linewidth=2.0, label="FSR: selected lowest request")
    axes[0].plot(t, result.valve_pu, color="tab:green", label="Actual valve stroke")
    axes[0].axhline(params.Vmax_pu, color="0.35", linestyle=":", linewidth=1.0, label="Valve maximum")
    axes[0].axhline(params.Vmin_pu, color="0.55", linestyle=":", linewidth=1.0, label="Valve minimum")
    setup_axis(axes[0], "Time after load step (s)", "Fuel stroke command (pu)")
    axes[0].set_title("GGOV1 low-value select: the lowest branch controls fuel")
    axes[0].legend(fontsize=8, ncol=2)

    i_explain = nearest_index(t, 1.0)
    callout(
        axes[0],
        "The thick black FSR trace sits on\nthe lowest colored request.\nThat branch is limiting fuel.",
        (t[i_explain], result.fsr_pu[i_explain]),
        (3.2, result.fsr_pu[i_explain] + 0.22),
    )
    valve_gap = np.abs(result.valve_pu - result.fsr_pu)
    visible = (t >= 0.0) & (t <= 10.0)
    i_gap_local = int(np.argmax(valve_gap[visible]))
    i_gap = int(np.flatnonzero(visible)[i_gap_local])
    callout(
        axes[0],
        "The green valve cannot jump to FSR:\nactuator lag and rate limits delay it.",
        (t[i_gap], result.valve_pu[i_gap]),
        (6.0, result.valve_pu[i_gap] - 0.20),
        color="tab:green",
    )

    axes[1].step(t, active, where="post", color="k")
    setup_axis(axes[1], "Time after load step (s)", "Selected branch")
    axes[1].set_yticks([0, 1, 2])
    axes[1].set_yticklabels(["FSRN", "FSRA", "FSRT"])
    axes[1].set_ylim(-0.4, 2.4)
    axes[1].set_xlim(0.0, 10.0)
    focus_note(
        axes[1],
        "Selected branch row:\nFSRN = normal speed/power control\nFSRA = acceleration protection\nFSRT = thermal/load protection",
        xy_axes=(0.98, 0.08),
    )

    outputs = [finish(fig, outdir / "ggov1_02_controller_arbitration.png", dpi)]
    if export_csv:
        outputs.append(export_response_csv(
            "ggov1_02_controller_arbitration",
            result,
            outdir / "ggov1_explainer_signals",
            active_branch=active,
        ))
    return outputs


def plot_droop_vs_isochronous(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    droop_params = GGOV1Params.lm2500_overrides(rselect=1)
    iso_params = GGOV1Params.lm2500_overrides(rselect=0)
    droop = step_response(
        11.5, 14.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=200.0,
        sample_dt_s=0.05,
        params=droop_params,
    )
    iso = step_response(
        11.5, 14.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=200.0,
        sample_dt_s=0.05,
        params=iso_params,
    )
    t_droop = rel_time(droop)
    t_iso = rel_time(iso)

    fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.8), sharex=True)
    axes[0].plot(t_droop, droop.freq_hz, color="tab:blue", label="Droop mode frequency")
    axes[0].plot(t_iso, iso.freq_hz, color="tab:green", label="Isochronous frequency")
    axes[0].axhline(60.0, color="k", linestyle=":", linewidth=1.0, label="Nominal 60 Hz")
    setup_axis(axes[0], "Time after load step (s)", "Frequency (Hz)")
    axes[0].set_title("Droop mode allows steady frequency offset; isochronous returns to nominal")
    axes[0].legend(fontsize=8)

    i_droop_end = nearest_index(t_droop, 180.0)
    i_iso_end = nearest_index(t_iso, 180.0)
    callout(
        axes[0],
        f"Droop stops with an intentional\nfrequency offset ({droop.freq_hz[i_droop_end]:.3f} Hz).",
        (t_droop[i_droop_end], droop.freq_hz[i_droop_end]),
        (108.0, 59.68),
        color="tab:blue",
    )
    callout(
        axes[0],
        "Isochronous control keeps integrating\nuntil frequency returns to 60 Hz.",
        (t_iso[i_iso_end], iso.freq_hz[i_iso_end]),
        (104.0, 59.90),
        color="tab:green",
    )

    axes[1].plot(t_droop, droop.Pm_mw, color="tab:blue", label="Droop mode mechanical power")
    axes[1].plot(t_iso, iso.Pm_mw, color="tab:green", label="Isochronous mechanical power")
    axes[1].step(t_droop, droop.Pe_demand_mw, where="post", color="k", linestyle="--", label="Load demand")
    setup_axis(axes[1], "Time after load step (s)", "Mechanical power (MW)")
    axes[1].legend(fontsize=8)
    axes[1].set_xlim(-1.0, 195.0)
    focus_note(
        axes[1],
        "Same load step, different objective:\ndroop supports power sharing; isochronous restores frequency",
    )

    outputs = [finish(fig, outdir / "ggov1_03_droop_vs_isochronous.png", dpi)]
    if export_csv:
        csv_dir = outdir / "ggov1_explainer_signals"
        outputs.append(export_response_csv("ggov1_03_droop_mode", droop, csv_dir))
        outputs.append(export_response_csv("ggov1_03_isochronous_mode", iso, csv_dir))
    return outputs


def plot_thermal_limiter_takeover(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    params = GGOV1Params.lm2500_overrides()
    result = step_response(
        15.0, 25.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=220.0,
        sample_dt_s=0.05,
        params=params,
    )
    t = rel_time(result)

    fig, axes = plt.subplots(3, 1, figsize=(9.5, 8.5), sharex=True)
    axes[0].step(t, result.Pe_demand_mw, where="post", color="k", linestyle="--",
                 label="Requested electrical load")
    axes[0].plot(t, result.Pm_mw, color="tab:blue", label="Mechanical turbine power")
    axes[0].axhline(params.Ldref * params.Trate_mw, color="tab:red", linestyle=":",
                    linewidth=1.2, label="Continuous thermal limit")
    axes[0].axhline(params.Pm_transient_max_pu * params.Trate_mw, color="tab:purple",
                    linestyle=":", linewidth=1.2, label="Transient valve-limited ceiling")
    setup_axis(axes[0], "Time after load step (s)", "Power (MW)")
    axes[0].set_title("Overload request: transient power is not continuous capability")
    axes[0].legend(fontsize=8, ncol=2)

    post_step = t >= 0.0
    i_peak_local = int(np.argmax(result.Pm_mw[post_step]))
    i_peak = int(np.flatnonzero(post_step)[i_peak_local])
    callout(
        axes[0],
        "Valve headroom can provide a\nshort burst above the 22 MW\ncontinuous thermal rating.",
        (t[i_peak], result.Pm_mw[i_peak]),
        (35.0, result.Pm_mw[i_peak] + 1.0),
        color="tab:blue",
    )

    active = selected_branch(result)
    takeover = first_sustained_index((t > 1.0) & (active == 2), samples=20)
    if takeover is not None:
        for ax in axes:
            ax.axvline(t[takeover], color="tab:purple", linestyle="--", linewidth=1.0,
                       alpha=0.75)
        axes[0].text(
            t[takeover] + 3.0,
            params.Ldref * params.Trate_mw - 1.4,
            "FSRT takeover",
            color="tab:purple",
            fontsize=8,
            bbox=FOCUS_BOX,
        )
        callout(
            axes[1],
            "FSRT falls below FSRN, so the\ntemperature branch becomes the\nwinning (most restrictive) command.",
            (t[takeover], result.fsrt_pu[takeover]),
            (t[takeover] + 35.0, result.fsrt_pu[takeover] + 0.10),
            color="tab:purple",
        )

    axes[1].plot(t, result.fsrn_pu, color="tab:blue", label="Speed/power request")
    axes[1].plot(t, result.fsrt_pu, color="tab:purple", label="Temperature/load limiter request")
    axes[1].plot(t, result.fsr_pu, color="k", linewidth=2.0, label="Selected request")
    axes[1].plot(t, result.valve_pu, color="tab:green", label="Actual valve stroke")
    setup_axis(axes[1], "Time after load step (s)", "Fuel stroke command (pu)")
    axes[1].set_title("Temperature branch eventually becomes the restrictive command")
    axes[1].legend(fontsize=8)

    axes[2].plot(t, result.freq_hz, color="tab:green", label="Generator frequency")
    axes[2].axhline(60.0, color="k", linestyle=":", linewidth=1.0, label="Nominal 60 Hz")
    setup_axis(axes[2], "Time after load step (s)", "Frequency (Hz)")
    axes[2].legend(fontsize=8)
    axes[2].set_xlim(-1.0, 215.0)
    focus_note(
        axes[2],
        "The 25 MW request exceeds sustained capability.\nThe limiter protects the turbine; it cannot guarantee 60 Hz.",
    )

    outputs = [finish(fig, outdir / "ggov1_04_thermal_limiter_takeover.png", dpi)]
    if export_csv:
        outputs.append(export_response_csv("ggov1_04_thermal_limiter_takeover", result, outdir / "ggov1_explainer_signals"))
    return outputs


def plot_acceleration_limiter(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    params = GGOV1Params.lm2500_overrides()
    result = step_response(
        15.0, 8.0,
        t_step_s=DEFAULT_STEP_TIME_S,
        t_end_s=30.0,
        sample_dt_s=0.01,
        params=params,
    )
    t = rel_time(result)
    domega_dt_pu_s = np.gradient(result.omega_pu, result.t_s)

    fig, axes = plt.subplots(3, 1, figsize=(9.5, 8.5), sharex=True)
    axes[0].plot(t, result.freq_hz, color="tab:green", label="Generator frequency")
    axes[0].axhline(60.0, color="k", linestyle=":", linewidth=1.0, label="Nominal 60 Hz")
    setup_axis(axes[0], "Time after load rejection (s)", "Frequency (Hz)")
    axes[0].set_title("Load rejection creates an overspeed tendency")
    axes[0].legend(fontsize=8)

    post_step = t >= 0.0
    i_fmax_local = int(np.argmax(result.freq_hz[post_step]))
    i_fmax = int(np.flatnonzero(post_step)[i_fmax_local])
    callout(
        axes[0],
        "Electrical load suddenly falls, but\nturbine power cannot disappear instantly.\nThe excess torque accelerates the rotor.",
        (0.05, result.freq_hz[nearest_index(t, 0.05)]),
        (5.0, 61.4),
    )
    callout(
        axes[0],
        "Peak frequency marks the end\nof the initial overspeed rise.",
        (t[i_fmax], result.freq_hz[i_fmax]),
        (10.0, 63.35),
        color="tab:green",
    )

    axes[1].plot(t, domega_dt_pu_s, color="tab:red", label="Estimated rotor acceleration")
    axes[1].axhline(params.aset_pu_s, color="k", linestyle=":", linewidth=1.0,
                    label="Acceleration setpoint")
    axes[1].axhline(0.0, color="0.4", linestyle=":", linewidth=1.0, label="Zero acceleration")
    setup_axis(axes[1], "Time after load rejection (s)", "Acceleration (pu/s)")
    axes[1].legend(fontsize=8)

    i_amax_local = int(np.argmax(domega_dt_pu_s[post_step]))
    i_amax = int(np.flatnonzero(post_step)[i_amax_local])
    callout(
        axes[1],
        "Positive acceleration above the setpoint\nasks the protective branch to cut fuel.",
        (t[i_amax], domega_dt_pu_s[i_amax]),
        (4.2, domega_dt_pu_s[i_amax] * 0.78),
        color="tab:red",
    )

    axes[2].plot(t, result.fsrn_pu, color="tab:blue", label="Speed/power request")
    axes[2].plot(t, result.fsra_pu, color="tab:red", label="Acceleration limiter request")
    axes[2].plot(t, result.fsr_pu, color="k", linewidth=2.0, label="Selected request")
    axes[2].plot(t, result.valve_pu, color="tab:green", label="Actual valve stroke")
    setup_axis(axes[2], "Time after load rejection (s)", "Fuel stroke command (pu)")
    axes[2].set_title("Acceleration branch is the fast protective fuel limiter")
    axes[2].legend(fontsize=8)
    axes[2].set_xlim(-1.0, 25.0)

    active = selected_branch(result)
    accel_segment = first_true_segment((t >= 0.0) & (active == 1), minimum_samples=5)
    if accel_segment is not None:
        i_start, i_stop = accel_segment
        for ax in axes:
            ax.axvspan(t[i_start], t[i_stop], color="tab:red", alpha=0.08, linewidth=0)
        i_mid = (i_start + i_stop) // 2
        callout(
            axes[2],
            "Shaded interval: FSRA is lowest.\nBlack FSR follows red FSRA,\ncommanding a rapid fuel reduction.",
            (t[i_mid], result.fsr_pu[i_mid]),
            (min(t[i_stop] + 3.0, 18.0), result.fsr_pu[i_mid] + 0.16),
            color="tab:red",
        )
    focus_note(
        axes[2],
        "Protection logic acts on acceleration first;\nfrequency responds afterward.",
    )

    outputs = [finish(fig, outdir / "ggov1_05_acceleration_limiter.png", dpi)]
    if export_csv:
        outputs.append(export_response_csv(
            "ggov1_05_acceleration_limiter",
            result,
            outdir / "ggov1_explainer_signals",
            domega_dt_pu_s=domega_dt_pu_s,
        ))
    return outputs


def plot_validation_context(outdir: Path, dpi: int) -> list[Path]:
    anchors = {
        "Hannett typical anchor": (1.140, -0.0039),
        "Hannett field-derived anchor": (2.320, -0.0076),
    }
    computed = hannett_part_a()
    names = list(anchors) + list(computed)
    t60 = [anchors[name][0] if name in anchors else computed[name][0] for name in names]
    excursions = [anchors[name][1] if name in anchors else computed[name][1] for name in names]
    y = np.arange(len(names))

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.2), sharey=True)
    reference_colors = ["0.62" if name in anchors else "tab:blue" for name in names]
    excursion_colors = ["0.62" if name in anchors else "tab:orange" for name in names]
    bars_t60 = axes[0].barh(y, t60, color=reference_colors, alpha=0.85)
    axes[0].set_xlabel("Time to reach 0.6 pu mechanical power (s)")
    axes[0].set_ylabel("Model")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(names)
    axes[0].grid(axis="x", alpha=0.3)
    axes[0].set_title("Benchmark response speed\n(shorter bar = faster response)")
    axes[0].bar_label(bars_t60, fmt="%.3f s", padding=3, fontsize=8)

    bars_excursion = axes[1].barh(y, excursions, color=excursion_colors, alpha=0.85)
    axes[1].axvline(0.0, color="k", linewidth=1.0)
    axes[1].set_xlabel("Maximum rotor speed excursion (pu)")
    axes[1].grid(axis="x", alpha=0.3)
    axes[1].set_title("Frequency dip during the benchmark step\n(closer to zero = smaller dip)")
    axes[1].bar_label(bars_excursion, fmt="%.4f pu", padding=3, fontsize=8)

    for ax in axes:
        ax.axhline(1.5, color="0.35", linestyle="--", linewidth=0.8)
    axes[0].legend(
        handles=[
            Patch(facecolor="0.62", label="Published Hannett anchors"),
            Patch(facecolor="tab:blue", label="Repository model cases"),
        ],
        loc="upper right",
        fontsize=8,
    )

    return [finish(fig, outdir / "ggov1_06_validation_context.png", dpi)]


def generate_all(outdir: Path, dpi: int, export_csv: bool) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    outputs.extend(plot_load_step_story(outdir, dpi, export_csv))
    outputs.extend(plot_controller_arbitration(outdir, dpi, export_csv))
    outputs.extend(plot_droop_vs_isochronous(outdir, dpi, export_csv))
    outputs.extend(plot_thermal_limiter_takeover(outdir, dpi, export_csv))
    outputs.extend(plot_acceleration_limiter(outdir, dpi, export_csv))
    outputs.extend(plot_validation_context(outdir, dpi))
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help="Directory for generated figures. Default: docs/figs")
    parser.add_argument("--dpi", type=int, default=160,
                        help="PNG output resolution. Default: 160")
    parser.add_argument("--export-csv", action="store_true",
                        help="Also export the simulated signals used by the plots.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outputs = generate_all(args.outdir, args.dpi, args.export_csv)
    print("Generated GGOV1 explainer outputs:")
    for path in outputs:
        print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())