from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

SCRIPTS = [
    "plot_ai_workload_story.py",
    "plot_frequency_trip_story.py",
    "plot_bess_power_buffer.py",
    "plot_correlation_sweep.py",
    "plot_fleet_tradeoffs.py",
    "plot_islanding_resiliency.py",
    "plot_architecture_tradeoff_map.py",
]


def main() -> None:
    for script in SCRIPTS:
        print(f"\n== {script} ==")
        subprocess.run([sys.executable, str(SCRIPT_DIR / script)], check=True)


if __name__ == "__main__":
    main()