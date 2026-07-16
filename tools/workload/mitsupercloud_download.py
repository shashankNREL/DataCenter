import os
import boto3
from botocore import UNSIGNED
from botocore.config import Config
import pandas as pd
import numpy as np

# --- 1. Anonymous S3 Downloader ---
def download_supercloud_data(filename, target_dir="data"):
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        
    local_path = os.path.join(target_dir, filename)
    if os.path.exists(local_path):
        print(f"File {filename} already exists locally. Skipping download.")
        return local_path

    print(f"Downloading {filename} from Open AWS S3 bucket...")
    s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
    bucket_name = "mit-supercloud-dataset"
    s3_key = f"2022-hpca/{filename}"
    
    s3.download_file(bucket_name, s3_key, local_path)
    print(f"Successfully saved to {local_path}")
    return local_path

# Fetch the dataset dependencies
scheduler_path = download_supercloud_data("scheduler_data.csv")
dcgm_path = download_supercloud_data("dcgm.csv")

# --- 2. Load and Filter Data ---
print("Loading data into memory...")
scheduler_df = pd.read_csv(scheduler_path)
dcgm_df = pd.read_csv(dcgm_path)

# Ensure proper time formats
dcgm_df['timestamp'] = pd.to_datetime(dcgm_df['timestamp'])
scheduler_df['start_time'] = pd.to_datetime(scheduler_df['start_time'])

# Define a specific time window to generate the snapshot (e.g., a high-activity 10-minute window)
window_start = dcgm_df['timestamp'].min() + pd.Timedelta(hours=2)
window_end = window_start + pd.Timedelta(minutes=10)

print(f"Generating electrical load snapshot from {window_start} to {window_end}...")
snapshot_dcgm = dcgm_df[(dcgm_df['timestamp'] >= window_start) & (dcgm_df['timestamp'] <= window_end)].copy()

# --- 3. High-Frequency Grid Resampling & Scaling ---
# Define millisecond timeline (100ms steps match native nvidia-smi/dcgm tracking precision)
ms_index = pd.date_range(start=window_start, end=window_end, freq='100L') # '100L' = 100 milliseconds
grid_timeline = pd.DataFrame(0.0, index=ms_index, columns=['it_power_w'])

# Group by the exact timestamp to capture concurrent aggregate load of the native trace
raw_timeline = snapshot_dcgm.groupby('timestamp')['power_w'].sum()

# Align the trace to our rigid 100ms grid (forward-filling momentary gaps, interpolating steps)
grid_timeline['it_power_w'] = raw_timeline.reindex(ms_index).interpolate(method='linear').fillna(0)

# --- 4. Scale to Moderate-Scale AI Data Center (e.g., ~15-20 Megawatt facility) ---
# The original dataset comes from an institutional cluster (~480 nodes). 
# We apply a scaling multiplier to elevate this behavior into a dedicated multi-megawatt AI cluster topology.
FACILITY_SCALE_MULTIPLIER = 12.5  
PUE = 1.25                        # Accounts for lagging overhead of liquid/air cooling pumps and transformers

grid_timeline['total_facility_w'] = grid_timeline['it_power_w'] * FACILITY_SCALE_MULTIPLIER * PUE
grid_timeline['total_facility_mw'] = grid_timeline['total_facility_w'] / 1e6

# --- 5. Compute Electrical Transience (dP/dt) ---
# Calculates Megawatts per second variation to assess grid-stability and voltage sag risks
grid_timeline['time_delta_sec'] = grid_timeline.index.to_series().diff().dt.total_seconds()
grid_timeline['dp_dt_mw_per_sec'] = grid_timeline['total_facility_mw'].diff() / grid_timeline['time_delta_sec']

# --- 6. Print Profile Diagnostics ---
print("\n--- ELECTRICAL TRANSISTOR PROFILE SUMMARY ---")
print(f"Peak Facility Demand:   {grid_timeline['total_facility_mw'].max():.2f} MW")
print(f"Minimum Facility Demand:{grid_timeline['total_facility_mw'].min():.2f} MW")
print(f"Max Power Delta (dP/dt): {grid_timeline['dp_dt_mw_per_sec'].abs().max():.2f} MW/sec")

print("\nSample 100ms Telemetry Timeline:")
print(grid_timeline[['total_facility_mw', 'dp_dt_mw_per_sec']].head(10))
