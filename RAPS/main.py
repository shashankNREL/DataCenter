""" Shortest-job first (SJF) job schedule simulator """

import argparse
import json
import numpy as np
import random
import pandas as pd
import os
import re
import sys
import time

from tqdm import tqdm
from raps.policy import PolicyType

# Check for the required Python version
required_major, required_minor = 3, 9

if sys.version_info < (required_major, required_minor):
    sys.stderr.write(f"Error: RAPS requires Python {required_major}.{required_minor} or greater\n")
    sys.exit(1)

parser = argparse.ArgumentParser(description='Resource Allocator & Power Simulator (RAPS)')
parser.add_argument('-c', '--cooling', action='store_true', help='Include FMU cooling model')
parser.add_argument('--start', type=str, help='ISO8061 string for start of simulation')
parser.add_argument('--end', type=str, help='ISO8061 string for end of simulation')
parser.add_argument('-d', '--debug', action='store_true', help='Enable debug mode and disable rich layout')
parser.add_argument('-e', '--encrypt', action='store_true', help='Encrypt any sensitive data in telemetry')
parser.add_argument('-n', '--numjobs', type=int, default=1000, help='Number of jobs to schedule')
parser.add_argument('-t', '--time', type=str, default=None, help='Length of time to simulate, e.g., 123, 123s, 27m, 3h, 7d')
parser.add_argument('-ff', '--fastforward', type=str, default=None, help='Fast-forward by time amount (uses same units as -t)')
parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
parser.add_argument('--seed', action='store_true', help='Set random number seed for deterministic simulation')
parser.add_argument('-f', '--replay', nargs='+', type=str, help='Either: path/to/joblive path/to/jobprofile' + \
                                                                ' -or- filename.npz (overrides --workload option)')
parser.add_argument('--reschedule', action='store_true', help='Reschedule the telemetry workload')
parser.add_argument('-u', '--uncertainties', action='store_true',
                    help='Change from floating point units to floating point units with uncertainties.' + \
                                                                ' Very expensive w.r.t simulation time!')
parser.add_argument('--jid', type=str, default='*', help='Replay job id')
parser.add_argument('--validate', action='store_true', help='Use node power instead of CPU/GPU utilizations')
parser.add_argument('-o', '--output', action='store_true', help='Output power, cooling, and loss models for later analysis')
parser.add_argument('-p', '--plot', nargs='+', choices=['power', 'loss', 'pue', 'temp', 'util'],
                    help='Specify one or more types of plots to generate: power, loss, pue, util, temp')
choices = ['png', 'svg', 'jpg', 'pdf', 'eps']
parser.add_argument('--imtype', type=str, choices=choices, default=choices[0], help='Plot image type')
parser.add_argument('--system', type=str, default='frontier', help='System config to use')
choices = [policy.value for policy in PolicyType]
parser.add_argument('-s', '--schedule', type=str, choices=choices, default=choices[0], help='Schedule policy to use')
choices = ['random', 'benchmark', 'peak', 'idle']
parser.add_argument('-w', '--workload', type=str, choices=choices, default=choices[0], help='Type of synthetic workload')
choices = ['layout1', 'layout2']
parser.add_argument('--layout', type=str, choices=choices, default=choices[0], help='Layout of UI')
args = parser.parse_args()
args_dict = vars(args)
print(args_dict)

from raps.config import ConfigManager
from raps.constants import OUTPUT_PATH
from raps.cooling import ThermoFluidsModel
from raps.ui import LayoutManager
from raps.flops import FLOPSManager
from raps.plotting import Plotter
from raps.power import PowerManager, compute_node_power, compute_node_power_validate
from raps.power import compute_node_power_uncertainties, compute_node_power_validate_uncertainties
from raps.scheduler import Scheduler, Job
from raps.telemetry import Telemetry
from raps.workload import Workload
from raps.weather import Weather
from raps.utils import create_casename, convert_to_seconds, write_dict_to_file, next_arrival

config = ConfigManager(system_name=args.system).get_config()

if args.seed:
    random.seed(SEED)
    np.random.seed(SEED)

if args.cooling:
    cooling_model = ThermoFluidsModel(**config)
    cooling_model.initialize()
    args.layout = "layout2"

    if args_dict['start']:
        cooling_model.weather = Weather(args_dict['start'], config = config)
else:
    cooling_model = None

if args.validate:
    if args.uncertainties:
        power_manager = PowerManager(compute_node_power_validate_uncertainties, **config)
    else:
        power_manager = PowerManager(compute_node_power_validate, **config)
else:
    if args.uncertainties:
        power_manager = PowerManager(compute_node_power_uncertainties, **config)
    else:
        power_manager = PowerManager(compute_node_power, **config)

flops_manager = FLOPSManager(**config)
args_dict['config'] = config
sc = Scheduler(
    power_manager = power_manager, flops_manager = flops_manager,
    cooling_model = cooling_model,
    **args_dict,
)
layout_manager = LayoutManager(args.layout, scheduler = sc, debug = args.debug, **config)

if args.replay:

    if args.fastforward: args.fastforward = convert_to_seconds(args.fastforward)

    td = Telemetry(**args_dict)

    # Try to extract date from given name to use as case directory
    matched_date = re.search(r"\d{4}-\d{2}-\d{2}", args.replay[0])
    if matched_date:
        extracted_date = matched_date.group(0)
        DIR_NAME = "sim=" + extracted_date
    else:
        extracted_date = "Date not found"
        DIR_NAME = create_casename()

    # Read either npz file or telemetry parquet files
    if args.replay[0].endswith(".npz"):
        print(f"Loading {args.replay[0]}...")
        jobs = td.load_snapshot(args.replay[0])
        if args.reschedule:
            for job in tqdm(jobs, desc="Updating requested_nodes"):
                job['requested_nodes'] = None
                job['submit_time'] = next_arrival()
    else:
        print(*args.replay)
        jobs = td.load_data(args.replay)
        td.save_snapshot(jobs, filename=DIR_NAME)

    # Set number of timesteps based on the last job running which we assume
    # is the maximum value of submit_time + wall_time of all the jobs
    if args.time:
        timesteps = convert_to_seconds(args.time)
    else:
        timesteps = int(max(job['wall_time'] + job['submit_time'] for job in jobs)) + 1

    print(f'Simulating {len(jobs)} jobs for {timesteps} seconds')
    time.sleep(1)

else:
    wl = Workload(**config)
    jobs = getattr(wl, args.workload)(num_jobs=args.numjobs)

    if args.verbose:
        for job_vector in jobs:
            job = Job(job_vector, 0)
            print('jobid:', job.id, '\tlen(gpu_trace):', len(job.gpu_trace), '\twall_time(s):', job.wall_time)
        time.sleep(2)

    if args.time:
        timesteps = convert_to_seconds(args.time)
    else:
        timesteps = 88200 # 24 hours

    DIR_NAME = create_casename()

OPATH = OUTPUT_PATH / DIR_NAME
print("Output directory is: ", OPATH)
sc.opath = OPATH

if args.plot or args.output:
    try:
        os.makedirs(OPATH)
    except OSError as error:
        print(f"Error creating directory: {error}")

if args.verbose:
    print(jobs)

layout_manager.run(jobs, timesteps=timesteps)

output_stats = sc.get_stats()
# Following b/c we get the following error when we use PM100 telemetry dataset
# TypeError: Object of type int64 is not JSON serializable
try:
    print(json.dumps(output_stats, indent=4))
except:
    print(output_stats)

if args.plot:
    if 'power' in args.plot:
        pl = Plotter('Time (s)', 'Power (kW)', 'Power History', \
                     OPATH / f'power.{args.imtype}', \
                     uncertainties=args.uncertainties)
        x, y = zip(*power_manager.history)
        pl.plot_history(x, y)

    if 'util' in args.plot:
        pl = Plotter('Time (s)', 'System Utilization (%)', \
                     'System Utilization History', OPATH / f'util.{args.imtype}')
        x, y = zip(*sc.sys_util_history)
        pl.plot_history(x, y)

    if 'loss' in args.plot:
        pl = Plotter('Time (s)', 'Power Losses (kW)', 'Power Loss History', \
                     OPATH / f'loss.{args.imtype}', \
                     uncertainties=args.uncertainties)
        x, y = zip(*power_manager.loss_history)
        pl.plot_history(x, y)

        pl = Plotter('Time (s)', 'Power Losses (%)', 'Power Loss History', \
                     OPATH / f'loss_pct.{args.imtype}', \
                     uncertainties=args.uncertainties)
        x, y = zip(*power_manager.loss_history_percentage)
        pl.plot_history(x, y)

    if 'pue' in args.plot:
        if cooling_model:
            ylabel = 'PUE_Out[1]'
            title = 'FMU ' + ylabel + 'History'
            pl = Plotter('Time (s)', ylabel, title, OPATH / f'pue.{args.imtype}', \
                         uncertainties=args.uncertainties)
            df = pd.DataFrame(cooling_model.fmu_history)
            df.to_parquet('cooling_model.parquet', engine='pyarrow')
            pl.plot_history(df['time'], df[ylabel])
        else:
            print('Cooling model not enabled... skipping output of plot')

    if 'temp' in args.plot:
        if cooling_model:
            ylabel = 'Tr_pri_Out[1]'
            title = 'FMU ' + ylabel + 'History'
            pl = Plotter('Time (s)', ylabel, title, OPATH / 'temp.svg')
            df = pd.DataFrame(cooling_model.fmu_history)
            df.to_parquet('cooling_model.parquet', engine='pyarrow')
            pl.plot_compare(df['time'], df[ylabel])
        else:
            print('Cooling model not enabled... skipping output of plot')

if args.output:

    if args.uncertainties:
        # Parquet cannot handle annotated ufloat format AFAIK
        print('Data dump not implemented using uncertainties!')  
    else:
        if cooling_model:
            df = pd.DataFrame(cooling_model.fmu_history)
            df.to_parquet(OPATH / 'cooling_model.parquet', engine='pyarrow')

        df = pd.DataFrame(power_manager.history)
        df.to_parquet(OPATH / 'power_history.parquet', engine='pyarrow')

        df = pd.DataFrame(power_manager.loss_history)
        df.to_parquet(OPATH / 'loss_history.parquet', engine='pyarrow')

        df = pd.DataFrame(sc.sys_util_history)
        df.to_parquet(OPATH / 'util.parquet', engine='pyarrow')

        try:
            with open(OPATH / 'stats.out', 'w') as f:
                json.dump(output_stats, f, indent=4)
        except:
            write_dict_to_file(output_stats, OPATH / 'stats.out')
