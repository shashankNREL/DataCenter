# data/

## Tracked
- `gas_turbine_surrogate.csv` — Phase 2 ThermoPower sweep output.
- `load17.csv` — reference load trace used by LM2500 dynamics notebooks.

## Not tracked (regenerable)
Downloaded on demand by `tools/workload/mitsupercloud_download.py` from the
public S3 bucket `s3://mit-supercloud-dataset/`:
- `dcgm.csv` — GPU power telemetry (~14 MB)
- `scheduler_data.csv` — job scheduler traces (~70 MB)
- `nvidia_smi_first_1gb.parquet` — nvidia-smi samples (~86 MB)

Run the downloader from repo root; see `tools/workload/mitsupercloud_notes.md`.
