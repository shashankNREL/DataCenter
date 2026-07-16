The dcgm.csv Extended Release (Recommended for Power Transients)
The most direct way to get native hardware power consumption data is through the HPCA '22 / Datacenter Challenge (DCC) extended release of the dataset (accessible via the open S3 bucket s3://mit-supercloud-dataset/2022-hpca/).
This format includes an explicit file called dcgm.csv (NVIDIA Data Center GPU Manager).  
GitHub
Column Available: Unlike raw nvidia-smi text outputs, DCGM polls telemetry at high speeds and outputs continuous physical power metrics directly in a designated column (usually logged as power draw in Watts or milliwatts).


# If using the dcgm.csv dataset, power is explicitly recorded by hardware counters
# Column names in DCGM logs typically follow standard telemetry naming
gpu_df = pd.read_csv(f"dcgm/{job_id}_dcgm.csv")

# Directly extract physical power rather than calculating it from utilization
gpu_df['power_w'] = gpu_df['power_usage_w'] * job['req_gpus']
