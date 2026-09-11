from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import lfilter


ROOT = Path(__file__).resolve().parents[2]
PRESENTATION_DIR = ROOT / "Presentation" / "ai_power_revolution"
FIGURE_DIR = PRESENTATION_DIR / "figures"
DATA_DIR = PRESENTATION_DIR / "data"


def ensure_output_dirs() -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def set_plot_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 140,
        "savefig.dpi": 220,
        "font.size": 12,
        "axes.titlesize": 15,
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.24,
        "lines.linewidth": 1.7,
    })


def save_figure(fig, name: str) -> Path:
    ensure_output_dirs()
    path = FIGURE_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    return path


def build_supercloud_trace(
    target_peak_mw: float = 18.0,
    pue: float = 1.25,
    window_hours: float = 2.0,
) -> dict[str, object]:
    """Rebuild the corrected MIT SuperCloud trace used by the notebook.

    The construction mirrors notebooks/lm2500_ai_workload.ipynb: per-GPU
    zero-order hold on a 100 ms grid, then additive PUE baseload, then a
    scale factor that places the facility peak at target_peak_mw.
    """
    parquet_path = ROOT / "data" / "nvidia_smi_first_1gb.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"Missing {parquet_path.relative_to(ROOT)}. Run the notebook data "
            "download cell or tools/workload/mitsupercloud_download.py first."
        )

    nv = pd.read_parquet(parquet_path)
    nv = nv.dropna(subset=["timestamp", "power_draw_W"]).copy()
    nv["timestamp"] = nv["timestamp"].astype(float)
    nv["power_draw_W"] = nv["power_draw_W"].astype(float)
    nv = nv.sort_values("timestamp").reset_index(drop=True)

    ts = nv["timestamp"].to_numpy()
    bin_s = 60.0
    bins = np.arange(ts.min(), ts.max() + bin_s, bin_s)
    counts, _ = np.histogram(ts, bins=bins)
    win_bins = int(round(window_hours * 3600 / bin_s))
    if win_bins >= len(counts):
        raise RuntimeError(
            f"Sample spans only {len(counts) * bin_s / 3600:.1f} h; "
            f"need >= {window_hours:.1f} h."
        )
    window_sum = np.convolve(counts, np.ones(win_bins, dtype=int), mode="valid")
    best = int(np.argmax(window_sum))
    start_unix = float(bins[best])
    end_unix = start_unix + window_hours * 3600

    in_window = (ts >= start_unix) & (ts < end_unix)
    sub = nv.loc[in_window, ["timestamp", "power_draw_W", "Node", "gpu_index"]].copy()
    sub["slot"] = np.floor((sub["timestamp"] - start_unix) * 10.0).astype(np.int64)
    n_slots = int(window_hours * 3600 * 10)
    slot_idx = np.arange(n_slots, dtype=np.int64)

    grid_w_native = np.zeros(n_slots)
    for _, group in sub.groupby(["Node", "gpu_index"]):
        values = pd.Series(group["power_draw_W"].values, index=group["slot"].values)
        values = values[~values.index.duplicated(keep="last")]
        grid_w_native += values.reindex(slot_idx).ffill().fillna(0.0).values

    if not (grid_w_native > 0).any():
        raise RuntimeError("Aggregated grid is all zero; pick another byte range.")

    native_peak_w = float(grid_w_native.max())
    native_mean_w = float(grid_w_native.mean())
    scale = (target_peak_mw * 1e6) / (native_peak_w + (pue - 1.0) * native_mean_w)
    cooling_baseload_w = (pue - 1.0) * native_mean_w * scale
    facility_w = grid_w_native * scale + cooling_baseload_w
    p_mw = facility_w / 1e6
    t_sec = slot_idx / 10.0
    dpdt_mw_s = np.diff(p_mw) / np.diff(t_sec)

    return {
        "t_sec": t_sec,
        "p_mw": p_mw,
        "dpdt_mw_s": dpdt_mw_s,
        "grid_w_native": grid_w_native,
        "scale": scale,
        "cooling_baseload_w": cooling_baseload_w,
        "start_unix": start_unix,
        "end_unix": end_unix,
        "gpu_count": int(sub.groupby(["Node", "gpu_index"]).ngroups),
        "sample_count": int(window_sum[best]),
    }


def low_pass_split(p_mw: np.ndarray, t_sec: np.ndarray, tau_s: float) -> dict[str, np.ndarray]:
    dt = float(np.median(np.diff(t_sec)))
    alpha = dt / (tau_s + dt)
    p_gt = lfilter([alpha], [1, -(1 - alpha)], p_mw, zi=[p_mw[0] * (1 - alpha)])[0]
    p_batt = p_mw - p_gt
    e_batt_mwh = np.cumsum(p_batt) * dt / 3600.0
    return {
        "p_gt_mw": p_gt,
        "p_batt_mw": p_batt,
        "e_batt_mwh": e_batt_mwh,
    }


def bess_metrics(p_mw: np.ndarray, t_sec: np.ndarray, tau_s: float) -> dict[str, float | np.ndarray]:
    split = low_pass_split(p_mw, t_sec, tau_s)
    p_batt = split["p_batt_mw"]
    p_gt = split["p_gt_mw"]
    e_batt = split["e_batt_mwh"]
    dt = float(np.median(np.diff(t_sec)))

    power_mw = float(np.abs(p_batt).max())
    energy_mwh = float(e_batt.max() - e_batt.min())
    duration_s = float(3600.0 * energy_mwh / power_mw) if power_mw > 0 else 0.0
    c_rate = float(power_mw / energy_mwh) if energy_mwh > 0 else np.inf

    return {
        **split,
        "tau_s": float(tau_s),
        "bess_power_mw": power_mw,
        "bess_energy_mwh": energy_mwh,
        "equivalent_duration_s": duration_s,
        "equivalent_c_rate": c_rate,
        "gt_max_ramp_mw_s": float(np.abs(np.diff(p_gt)).max() / dt),
    }


def make_decorrelated_trace(p_mw: np.ndarray, copies: int = 64, seed: int = 7) -> np.ndarray:
    """Approximate many independent jobs by averaging shifted copies.

    This is a presentation-grade synthesis, not a statistically complete
    workload model. It keeps the same mean but reduces coherent fast swings.
    """
    rng = np.random.default_rng(seed)
    mean = float(p_mw.mean())
    deviations = p_mw - mean
    shifts = rng.integers(0, p_mw.size, size=copies)
    decorrelated = np.mean([np.roll(deviations, int(shift)) for shift in shifts], axis=0)
    return mean + decorrelated


def storage_duration_examples() -> pd.DataFrame:
    return pd.DataFrame([
        {"label": "4 h utility LFP", "duration_h": 4.0, "c_rate": 0.25},
        {"label": "2 h utility LFP", "duration_h": 2.0, "c_rate": 0.50},
        {"label": "1 h grid services", "duration_h": 1.0, "c_rate": 1.00},
        {"label": "15 min high-power BESS", "duration_h": 0.25, "c_rate": 4.00},
        {"label": "5 min UPS / flywheel class", "duration_h": 1 / 12, "c_rate": 12.0},
    ])