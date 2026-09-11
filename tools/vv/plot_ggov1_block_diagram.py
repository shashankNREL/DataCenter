"""Generate a publication-ready diagram of this repo's GGOV1 implementation.

The figure is an original, implementation-oriented drawing. It uses two panels
to keep the decision logic readable at IEEE two-column width and named
continuation connectors instead of long feedback lines through other blocks.

Run:
  pixi run vv-ggov-block

Outputs:
  docs/figs/ggov1_implementation_block_diagram.pdf
  docs/figs/ggov1_implementation_block_diagram.svg
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTDIR = ROOT / "docs" / "figs"
OUTPUT_STEM = "ggov1_implementation_block_diagram"

# IEEE two-column text width is approximately 7.16 in. Font sizes below are
# therefore final printed sizes; the figure should not need down-scaling.
FIGURE_SIZE_IN = (7.16, 6.15)

COLORS = {
    "speed": "#dbeafe",
    "accel": "#fee2e2",
    "temp": "#ede9fe",
    "select": "#fef3c7",
    "plant": "#dcfce7",
    "swing": "#e0f2fe",
    "io": "#ffffff",
    "note": "#f1f5f9",
    "edge": "#111827",
    "feedback": "#475569",
}

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans"],
        "font.size": 7.0,
        "mathtext.fontset": "dejavusans",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "axes.linewidth": 0.8,
    }
)


@dataclass(frozen=True)
class Block:
    x: float
    y: float
    width: float
    height: float


@dataclass
class BlockArtist:
    name: str
    patch: FancyBboxPatch
    text: plt.Text


def left(block: Block, fraction: float = 0.5) -> tuple[float, float]:
    return block.x, block.y + fraction * block.height


def right(block: Block, fraction: float = 0.5) -> tuple[float, float]:
    return block.x + block.width, block.y + fraction * block.height


def top(block: Block, fraction: float = 0.5) -> tuple[float, float]:
    return block.x + fraction * block.width, block.y + block.height


def bottom(block: Block, fraction: float = 0.5) -> tuple[float, float]:
    return block.x + fraction * block.width, block.y


def add_block(
    ax,
    artists: list[BlockArtist],
    name: str,
    block: Block,
    text: str,
    facecolor: str,
    fontsize: float = 6.6,
    linewidth: float = 0.85,
    bold_first_line: bool = False,
) -> Block:
    patch = FancyBboxPatch(
        (block.x, block.y),
        block.width,
        block.height,
        boxstyle="round,pad=0.025,rounding_size=0.035",
        facecolor=facecolor,
        edgecolor=COLORS["edge"],
        linewidth=linewidth,
        zorder=3,
    )
    ax.add_patch(patch)
    text_artist = ax.text(
        block.x + block.width / 2,
        block.y + block.height / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="semibold" if bold_first_line else "normal",
        linespacing=1.08,
        zorder=4,
    )
    artists.append(BlockArtist(name=name, patch=patch, text=text_artist))
    return block


def add_panel(ax, xy: tuple[float, float], width: float, height: float, title: str) -> None:
    x, y = xy
    panel = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.025,rounding_size=0.035",
        facecolor="white",
        edgecolor="#94a3b8",
        linewidth=0.8,
        zorder=0,
    )
    ax.add_patch(panel)
    ax.text(
        x + 0.12,
        y + height - 0.18,
        title,
        ha="left",
        va="center",
        fontsize=7.5,
        fontweight="bold",
        color=COLORS["edge"],
        zorder=5,
    )


def add_connector(
    ax,
    xy: tuple[float, float],
    text: str,
    facecolor: str = COLORS["io"],
    radius: float = 0.16,
    fontsize: float = 6.1,
) -> tuple[float, float]:
    circle = Circle(
        xy,
        radius,
        facecolor=facecolor,
        edgecolor=COLORS["edge"],
        linewidth=0.8,
        zorder=4,
    )
    ax.add_patch(circle)
    ax.text(xy[0], xy[1], text, ha="center", va="center", fontsize=fontsize, zorder=5)
    return xy


def add_arrow(
    ax,
    points: list[tuple[float, float]],
    label: str | None = None,
    label_xy: tuple[float, float] | None = None,
    dashed: bool = False,
    color: str = COLORS["edge"],
    linewidth: float = 0.8,
    fontsize: float = 6.2,
) -> None:
    if len(points) < 2:
        raise ValueError("add_arrow requires at least two points")

    linestyle = (0, (3, 2)) if dashed else "-"
    for start, end in zip(points[:-2], points[1:-1]):
        ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            color=color,
            linewidth=linewidth,
            linestyle=linestyle,
            zorder=1,
        )

    arrow = FancyArrowPatch(
        points[-2],
        points[-1],
        arrowstyle="-|>",
        mutation_scale=8.5,
        linewidth=linewidth,
        linestyle=linestyle,
        color=color,
        shrinkA=0,
        shrinkB=3,
        connectionstyle="arc3,rad=0",
        zorder=2,
    )
    ax.add_patch(arrow)

    if label:
        if label_xy is None:
            label_xy = (
                0.5 * (points[-2][0] + points[-1][0]),
                0.5 * (points[-2][1] + points[-1][1]),
            )
        ax.text(
            label_xy[0],
            label_xy[1],
            label,
            ha="center",
            va="center",
            fontsize=fontsize,
            color=color,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.35},
            zorder=5,
        )


def add_input_label(ax, xy: tuple[float, float], text: str, ha: str = "left") -> None:
    ax.text(
        xy[0],
        xy[1],
        text,
        ha=ha,
        va="center",
        fontsize=6.3,
        color=COLORS["edge"],
        zorder=5,
    )


def validate_block_text(fig, artists: list[BlockArtist], margin_px: float = 1.5) -> None:
    """Fail generation if any block label extends beyond its block."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    failures: list[str] = []
    for artist in artists:
        patch_box = artist.patch.get_window_extent(renderer)
        text_box = artist.text.get_window_extent(renderer)
        if (
            text_box.x0 < patch_box.x0 + margin_px
            or text_box.x1 > patch_box.x1 - margin_px
            or text_box.y0 < patch_box.y0 + margin_px
            or text_box.y1 > patch_box.y1 - margin_px
        ):
            failures.append(artist.name)
    if failures:
        joined = ", ".join(failures)
        raise RuntimeError(f"Text does not fit inside diagram block(s): {joined}")


def validate_page_text(fig, ax, margin_px: float = 1.0) -> None:
    """Fail generation if any text artist is clipped by the figure canvas."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas_width, canvas_height = fig.canvas.get_width_height()
    failures: list[str] = []
    for text_artist in ax.texts:
        text_box = text_artist.get_window_extent(renderer)
        if (
            text_box.x0 < margin_px
            or text_box.x1 > canvas_width - margin_px
            or text_box.y0 < margin_px
            or text_box.y1 > canvas_height - margin_px
        ):
            snippet = text_artist.get_text().replace("\n", " ")[:32]
            failures.append(snippet)
    if failures:
        joined = ", ".join(failures)
        raise RuntimeError(f"Text is clipped by the diagram page: {joined}")


def draw_diagram(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    outputs = [outdir / f"{OUTPUT_STEM}.pdf", outdir / f"{OUTPUT_STEM}.svg"]

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_IN)
    fig.subplots_adjust(left=0.015, right=0.995, bottom=0.02, top=0.995)
    ax.set_xlim(0.0, 12.0)
    ax.set_ylim(0.0, 10.0)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    block_artists: list[BlockArtist] = []

    add_panel(ax, (0.08, 5.18), 11.84, 4.74, "(a) Controller requests and low-value decision")
    add_panel(ax, (0.08, 0.72), 11.84, 4.22, "(b) Selected fuel path, turbine–generator dynamics, and feedback")

    # ------------------------------------------------------------------
    # Panel (a): three controller requests feed one low-value gate.
    # ------------------------------------------------------------------
    ax.text(0.25, 9.28, "Speed / power", fontsize=6.6, fontweight="bold", color="#1d4ed8")
    ax.text(0.25, 8.04, "Acceleration", fontsize=6.6, fontweight="bold", color="#b91c1c")
    ax.text(0.25, 6.78, "Temperature / load", fontsize=6.6, fontweight="bold", color="#6d28d9")

    speed_error = add_block(
        ax,
        block_artists,
        "speed error",
        Block(1.30, 8.24, 2.05, 0.88),
        "Speed/droop error\n$r_{select}$: $P_e$, valve, $fsrn$, or iso.\nclip to [MinERR, MaxERR]",
        COLORS["speed"],
        fontsize=6.0,
    )
    speed_pi = add_block(
        ax,
        block_artists,
        "speed PI",
        Block(4.05, 8.24, 1.55, 0.88),
        "Speed PI\n$K_{pgov}+K_{igov}/s$\n$fsrn$",
        COLORS["speed"],
        fontsize=6.3,
    )

    accel_filter = add_block(
        ax,
        block_artists,
        "acceleration filter",
        Block(1.30, 6.98, 1.72, 0.88),
        "Filtered derivative\n$\\dot\\omega_f=(\\omega-x_a)/T_a$\n$x_a$: speed lag",
        COLORS["accel"],
        fontsize=5.8,
    )
    accel_error = add_block(
        ax,
        block_artists,
        "acceleration error",
        Block(3.45, 6.98, 1.34, 0.88),
        "Accel. error\n$a_{set}-$\n$\\dot\\omega_f$",
        COLORS["accel"],
        fontsize=6.0,
    )
    accel_integrator = add_block(
        ax,
        block_artists,
        "acceleration integrator",
        Block(5.22, 6.98, 1.32, 0.88),
        "Integrator\n$K_a/s$\n$fsra$",
        COLORS["accel"],
        fontsize=6.3,
    )

    temp_shape = add_block(
        ax,
        block_artists,
        "temperature lead lag",
        Block(1.30, 5.72, 1.54, 0.88),
        "Fuel-temperature\n$(1+sT_{sa})/(1+sT_{sb})$",
        COLORS["temp"],
        fontsize=5.9,
    )
    temp_lag = add_block(
        ax,
        block_artists,
        "temperature lag",
        Block(3.22, 5.72, 1.20, 0.88),
        "Temperature lag\n$1/(1+sT_{fload})$\n$x_{tload}$",
        COLORS["temp"],
        fontsize=5.8,
    )
    temp_error = add_block(
        ax,
        block_artists,
        "temperature error",
        Block(4.80, 5.72, 1.65, 0.88),
        "Thermal error\n$t_{lim}-x_{tload}$\n$t_{lim}=Ldref/K_{turb}+W_{fnl}$",
        COLORS["temp"],
        fontsize=5.45,
    )
    temp_pi = add_block(
        ax,
        block_artists,
        "temperature PI",
        Block(6.83, 5.72, 1.48, 0.88),
        "Load/temp. PI\n$K_{pload}+K_{iload}/s$\n$fsrt$",
        COLORS["temp"],
        fontsize=5.9,
    )

    low_value_gate = add_block(
        ax,
        block_artists,
        "low value gate",
        Block(9.10, 6.52, 1.38, 2.08),
        "Low-value\nselect\n$fsr=$\nmin($fsrn$,\n$fsra$, $fsrt$)",
        COLORS["select"],
        fontsize=6.3,
    )
    anti_windup = add_block(
        ax,
        block_artists,
        "anti windup",
        Block(8.72, 8.82, 2.12, 0.78),
        "Back-calculation\nin each integrator\n$-K_{bc}(fsr^*-fsr)$",
        COLORS["note"],
        fontsize=5.45,
    )

    # Speed inputs are separate; Pref does not pass through rselect.
    add_input_label(ax, (0.34, 8.71), "$\\omega$, $Pref$")
    add_arrow(ax, [(1.05, 8.68), left(speed_error)])
    add_connector(ax, (0.96, 8.31), "B")
    add_arrow(ax, [(1.12, 8.31), (1.12, 8.45), left(speed_error, 0.25)], dashed=True, color=COLORS["feedback"])
    ax.text(0.42, 8.31, "selected\nfeedback", ha="center", va="center", fontsize=5.1)
    add_arrow(ax, [right(speed_error), left(speed_pi)])
    add_arrow(ax, [right(speed_pi), (8.68, 8.28), left(low_value_gate, 0.80)], label="$fsrn$", label_xy=(7.18, 8.44))

    add_connector(ax, (0.96, 7.42), "$\\omega$")
    add_arrow(ax, [(1.12, 7.42), left(accel_filter)])
    add_arrow(ax, [right(accel_filter), left(accel_error)])
    add_arrow(ax, [right(accel_error), left(accel_integrator)])
    add_arrow(ax, [right(accel_integrator), (8.68, 7.42), left(low_value_gate, 0.50)], label="$fsra$", label_xy=(7.60, 7.57))

    add_connector(ax, (0.96, 6.16), "$W_f$")
    add_arrow(ax, [(1.12, 6.16), left(temp_shape)])
    add_arrow(ax, [right(temp_shape), left(temp_lag)])
    add_arrow(ax, [right(temp_lag), left(temp_error)])
    add_arrow(ax, [right(temp_error), left(temp_pi)])
    add_arrow(ax, [right(temp_pi), (8.68, 6.16), left(low_value_gate, 0.20)], label="$fsrt$", label_xy=(8.56, 6.32))

    add_arrow(
        ax,
        [top(low_value_gate), bottom(anti_windup)],
        label="$fsr$",
        label_xy=(10.06, 8.76),
        dashed=True,
        color=COLORS["feedback"],
    )
    add_connector(ax, (11.12, 7.56), "A", COLORS["select"])
    add_arrow(ax, [right(low_value_gate), (10.78, 7.56), (10.96, 7.56)])
    ax.text(11.12, 7.91, "selected $fsr$", ha="center", va="center", fontsize=5.4)

    ax.text(
        0.34,
        5.36,
        "$r_{select}=-2$ (governor-output feedback) is solved algebraically in closed form. "
        "Dashed feedback denotes implementation-specific back-calculation.",
        ha="left",
        va="center",
        fontsize=5.55,
        color=COLORS["feedback"],
    )

    # ------------------------------------------------------------------
    # Panel (b): selected request drives the plant; named connectors feed
    # panel (a) without long crossing feedback lines.
    # ------------------------------------------------------------------
    add_connector(ax, (0.50, 3.72), "A", COLORS["select"])
    add_input_label(ax, (0.18, 4.36), "from low-value select")
    valve = add_block(
        ax,
        block_artists,
        "valve actuator",
        Block(0.92, 3.22, 1.42, 1.00),
        "Valve actuator\nclip $V_{min},V_{max}$\n$T_{act}$ + rate limits",
        COLORS["plant"],
        fontsize=5.9,
    )
    fuel_map = add_block(
        ax,
        block_artists,
        "fuel map",
        Block(2.72, 3.22, 1.42, 1.00),
        "Fuel map\n$W_f=valve\\,\\omega$\n($flag=1$)\n$D_m<0$: $\\omega^{D_m}$",
        COLORS["plant"],
        fontsize=5.2,
    )
    turbine = add_block(
        ax,
        block_artists,
        "turbine dynamics",
        Block(4.52, 3.22, 1.62, 1.00),
        "Turbine $T_b/T_c$\n$P_{m,t}=K_{turb}(x_t-W_{fnl})$\n$D_m>0$: speed damping",
        COLORS["plant"],
        fontsize=5.25,
    )
    base_conversion = add_block(
        ax,
        block_artists,
        "mechanical base conversion",
        Block(6.52, 3.30, 0.98, 0.84),
        "Base change\n$P_{m,g}=k_bP_{m,t}$\n$k_b=T_{rate}/S_n$",
        COLORS["note"],
        fontsize=5.35,
    )
    swing = add_block(
        ax,
        block_artists,
        "swing equation",
        Block(8.18, 3.02, 2.14, 1.40),
        "Swing equation\n(generator base)\n$M\\dot\\omega=P_{m,g}-P_{e,g}$\n$-D(\\omega-1)$\n$\\dot\\delta=\\omega_0(\\omega-1)$",
        COLORS["swing"],
        fontsize=5.5,
    )

    add_arrow(ax, [(0.66, 3.72), left(valve)])
    add_arrow(ax, [right(valve), left(fuel_map)], label="valve", label_xy=(2.54, 3.90))
    add_connector(ax, (3.43, 4.54), "$\\omega$")
    add_arrow(ax, [(3.43, 4.38), top(fuel_map)], dashed=True, color=COLORS["feedback"])
    add_arrow(ax, [right(fuel_map), left(turbine)], label="$W_f$", label_xy=(4.33, 3.90))
    add_arrow(ax, [right(turbine), left(base_conversion)], label="$P_{m,t}$", label_xy=(6.32, 3.91))
    add_arrow(ax, [right(base_conversion), left(swing)], label="$P_{m,g}$", label_xy=(7.84, 3.90))

    add_connector(ax, (10.92, 3.72), "$\\omega$")
    add_arrow(ax, [right(swing), (10.72, 3.72)])
    ax.text(11.18, 4.10, "feedback to $\\omega$\nconnectors", ha="center", va="center", fontsize=5.25)

    # Raw Wf feeds the temperature limiter; the reporting lag does not.
    add_connector(ax, (3.42, 2.77), "$W_f$")
    add_arrow(ax, [bottom(fuel_map), (3.43, 2.93)], dashed=True, color=COLORS["feedback"])
    add_input_label(ax, (3.66, 2.77), "to panel (a) temperature path")
    fuel_reporting = add_block(
        ax,
        block_artists,
        "fuel reporting lag",
        Block(0.92, 1.64, 1.58, 0.72),
        "Reporting only\n$1/(1+sT_{comb})$\n$\\rightarrow$ fuel kg/s",
        COLORS["note"],
        fontsize=5.35,
    )
    add_arrow(
        ax,
        [bottom(fuel_map), (3.43, 2.52), (1.71, 2.52), top(fuel_reporting)],
        dashed=True,
        color=COLORS["feedback"],
    )

    pe_demand = add_block(
        ax,
        block_artists,
        "electrical demand",
        Block(3.04, 1.40, 1.24, 0.72),
        "Demand\n$P_{e,load}$ (MW)",
        COLORS["io"],
        fontsize=5.9,
    )
    load_model = add_block(
        ax,
        block_artists,
        "load model",
        Block(4.66, 1.30, 1.74, 0.92),
        "Frequency-sensitive load\n$P_{e,g}=P_{e,load}[1+\\alpha(\\omega-1)]$\n(generator base)",
        COLORS["swing"],
        fontsize=5.35,
    )
    power_base = add_block(
        ax,
        block_artists,
        "electrical base conversion",
        Block(6.78, 1.34, 1.04, 0.84),
        "Base change\n$P_{e,t}=P_{e,g}/k_b$",
        COLORS["note"],
        fontsize=5.65,
    )
    power_lag = add_block(
        ax,
        block_artists,
        "electrical power lag",
        Block(8.20, 1.30, 1.34, 0.92),
        "Power transducer\n$1/(1+sT_{pelec})$\n$P_{e,filt}$",
        COLORS["swing"],
        fontsize=5.75,
    )

    add_arrow(ax, [right(pe_demand), left(load_model)])
    add_connector(ax, (5.53, 2.56), "$\\omega$")
    add_arrow(ax, [(5.53, 2.40), top(load_model)], dashed=True, color=COLORS["feedback"])
    add_arrow(ax, [right(load_model), left(power_base)], label="$P_{e,g}$", label_xy=(6.60, 1.92))
    add_arrow(ax, [right(power_base), left(power_lag)], label="$P_{e,t}$", label_xy=(8.02, 1.92))

    # Pe_gen enters the swing equation before the turbine-base conversion.
    add_arrow(
        ax,
        [top(load_model, 0.72), (5.91, 2.70), (8.72, 2.70), bottom(swing, 0.30)],
        label="$P_{e,g}$",
        label_xy=(7.18, 2.84),
        color=COLORS["feedback"],
    )
    add_connector(ax, (10.12, 1.76), "B")
    add_arrow(ax, [right(power_lag), (9.92, 1.76)])
    add_input_label(ax, (10.34, 1.76), "$P_{e,filt}$ to $r_{select}=1$")

    ax.text(
        0.28,
        0.93,
        "Bases: governor, valve, $W_f$, and $P_{m,t}$ use turbine base $T_{rate}$; "
        "$P_{e,g}$ and the swing equation use generator base $S_n$.\n"
        "Inactive subset: $K_{dgov}/T_{dgov}$, deadband, outer MW loop, "
        "$R_{up}/R_{down}$, and $T_{eng}$.",
        ha="left",
        va="center",
        fontsize=5.15,
        color=COLORS["feedback"],
    )

    validate_block_text(fig, block_artists)
    validate_page_text(fig, ax)

    metadata = {
        "Title": "GGOV1 implementation block diagram",
        "Author": "DataCenter model repository",
        "Subject": "Implementation-oriented GGOV1 governor and turbine-generator signal flow",
    }
    fig.savefig(outputs[0], format="pdf", metadata=metadata)
    fig.savefig(outputs[1], format="svg", metadata={"Title": metadata["Title"]})
    plt.close(fig)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Directory for generated PDF and SVG files. Default: docs/figs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outputs = draw_diagram(args.outdir)
    print("Generated publication-ready GGOV1 block diagrams:")
    for path in outputs:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        print(f"  {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())