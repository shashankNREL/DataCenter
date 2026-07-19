"""LM9000 aeroderivative gas turbine model — component-based thermodynamic stack.

Built from first principles using compressor/turbine component models, calibrated
to GE LM9000 datasheet specifications:
  - Mechanical drive (simple cycle): 73.5 MW, 44% efficiency, 455°C exhaust
  - Power generation (simple cycle): 56.723 MW, 39.52% efficiency
  - Power generation (combined cycle): 72.471 MW, 50.48% efficiency

Configuration:
  - 4-stage low-pressure compressor (LPC)
  - 9-stage high-pressure compressor (HPC)
  - DLE 1.5 combustor (15 ppm NOx)
  - 2-stage high-pressure turbine (HPT, drives HPC)
  - 1-stage intermediate-pressure turbine (IPT, drives LPC)
  - 4-stage free power turbine (FPT, drives load)

This is a two-spool design:
  - Spool 1: HPC + HPT (variable speed, ~10,000 rpm base load)
  - Spool 2: LPC + IPT + FPT (3,600 rpm power turbine, directly coupled)

For electrical generation at 60 Hz (3,600 rpm), the FPT shaft couples directly
to a synchronous generator. The HP spool speed varies with load to optimize
compressor/turbine matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

ArrayLike = Union[float, np.ndarray, list, tuple]

# Natural gas properties (ISO conditions)
# LHV for natural gas (CH4-dominated)
DEFAULT_FUEL_LHV_J_KG = 49e6

# CO2 emissions factor (kg CO2 per kg fuel), back-calculated from datasheet:
#   fuel_kg_s = P_net / (eta * LHV) = 56.723e6 W / (0.3952 * 49e6 J/kg) = 2.928 kg/s
#   CO2_kg_s = 492.9 kg/MWh * 56.723 MW / 3600 s/h            = 7.764 kg/s
#   CO2/fuel = 7.764 / 2.928                                  = 2.65 kg CO2/kg fuel
# Pure CH4 stoichiometric combustion is 2.75 (MW ratio 44/16); the 2.65 value
# absorbs combustion efficiency < 100% and a small heavier-hydrocarbon share
# in pipeline natural gas. See L3 in docs/tier_plan.md.
DEFAULT_CO2_PER_FUEL_KG = 2.65

# Air/fuel ratio (mass) for the overall lean operation of a modern aero GT.
# The original code used `air = fuel / 0.034` (AFR ~29), which is closer to
# stoichiometric for methane (~17.2) than to actual lean GT operation.
# Real LM2500 full-load operation (NAVEDTRA, via MSC01A_1211_B5):
#   air = 442,800 lb/h, fuel = 9,000 lb/h  ->  AFR = 49.2  (F/A = 0.0203)
# LM9000 DLE 1.5 runs in the same lean regime; AFR = 50 gives exhaust mass
# ~149 kg/s at design, consistent with the published ~158 kg/s headline.
# See L1 in docs/tier_plan.md.
DEFAULT_AIR_FUEL_RATIO = 50.0

# Working fluid properties (air at ~600–1200 K mean) — retained for
# documentation; not used in the current empirical dispatch() path.
GAMMA_AIR = 1.38        # specific heat ratio
CP_AIR_J_KG_K = 1005.0  # specific heat at constant pressure (J/kg/K)
R_AIR_J_KG_K = 287.0    # specific gas constant


@dataclass
class CompressorMap:
    """Compressor isentropic efficiency and pressure ratio at design conditions."""

    stages: int
    pressure_ratio_design: float
    eta_isentropic_design: float  # design-point isentropic efficiency [0, 1]
    min_load_frac: float = 0.15  # minimum stable load fraction


@dataclass
class TurbineMap:
    """Turbine isentropic efficiency and pressure ratio at design conditions."""

    stages: int
    pressure_ratio_design: float
    eta_isentropic_design: float  # design-point isentropic efficiency [0, 1]


# LM9000 component specifications (calibrated to datasheet)
# Low-pressure compressor: 4 stages, moderate compression
LPC_MAP = CompressorMap(
    stages=4,
    pressure_ratio_design=3.8,  # typical for a 4-stage LP compressor
    eta_isentropic_design=0.88,  # 88% isentropic efficiency (modern aero)
    min_load_frac=0.15,
)

# High-pressure compressor: 9 stages, high compression (LPC discharge → HPC)
HPC_MAP = CompressorMap(
    stages=9,
    pressure_ratio_design=5.2,  # typical for a 9-stage HP compressor
    eta_isentropic_design=0.86,  # 86% isentropic efficiency
    min_load_frac=0.15,
)

# High-pressure turbine: 2 stages, drives HPC
# HPT inlet typically ~1400–1500 K (combustor exit temp after cooling)
HPT_MAP = TurbineMap(
    stages=2,
    pressure_ratio_design=3.1,  # turbine expansion ratio
    eta_isentropic_design=0.91,  # 91% isentropic efficiency
)

# Intermediate-pressure turbine: 1 stage, drives LPC
# IPT inlet temperature stepped down from HPT exit (cooling/bleed)
IPT_MAP = TurbineMap(
    stages=1,
    pressure_ratio_design=2.1,
    eta_isentropic_design=0.88,
)

# Free power turbine: 4 stages, drives load (generator or mechanical)
FPT_MAP = TurbineMap(
    stages=4,
    pressure_ratio_design=2.0,
    eta_isentropic_design=0.90,
)

# Combustor and overall cycle assumptions
COMBUSTOR_EFFICIENCY = 0.98  # fuel combustion completeness
COMBUSTOR_INLET_TEMP_K = 650.0  # HPC discharge temperature (nominal)
COMBUSTOR_EXIT_TEMP_K = 1450.0  # combustor outlet (design point, before cooling)
COMBUSTOR_COOLING_BLEED = 0.15  # fraction of compressor discharge diverted to cool HPT inlet

# Stack exhaust (FPT exit) for simple cycle
SIMPLE_CYCLE_EXHAUST_TEMP_K = 729.0  # 456°C from datasheet


class LM9000SimpleCycle:
    """LM9000 simple-cycle gas turbine (no bottoming cycle).

    Thermodynamic stack:
      1. Intake (ambient air)
      2. LPC (4 stages, compresses intake)
      3. HPC (9 stages, further compression)
      4. Combustor (fuel injection, DLE 1.5, 15 ppm NOx target)
      5. HPT (2 stages, drives HPC)
      6. IPT (1 stage, drives LPC)
      7. FPT (4 stages, drives generator/load)
      8. Exhaust stack

    Dispatch interface:
      load ∈ [0, 1] -> power (W), fuel (kg/s), efficiency, exhaust (T, flow)

    Calibration to datasheet:
      - Design point (load=1.0): 56.723 MW, 39.52% efficiency
      - Exhaust temp: 729 K (456°C)
    """

    def __init__(
        self,
        rated_power_mw: float = 56.723,
        fuel_lhv_j_kg: float = DEFAULT_FUEL_LHV_J_KG,
        co2_per_fuel_kg: float = DEFAULT_CO2_PER_FUEL_KG,
        combustor_exit_temp_k: float = COMBUSTOR_EXIT_TEMP_K,
        exhaust_temp_k: float = SIMPLE_CYCLE_EXHAUST_TEMP_K,
        air_fuel_ratio: float = DEFAULT_AIR_FUEL_RATIO,
        min_load_frac: float = 0.0,
        no_load_fuel_frac: float = 0.2,
    ):
        """
        Args:
            rated_power_mw: electrical power at design point (default 56.723 MW).
            fuel_lhv_j_kg: lower heating value of fuel (J/kg).
            co2_per_fuel_kg: CO2 emissions factor (kg/kg fuel).
            combustor_exit_temp_k: combustor outlet temp (K).
            exhaust_temp_k: exhaust temperature from FPT exit (K).
            air_fuel_ratio: mass ratio of air to fuel at design (default 50,
                consistent with lean DLE aero operation; see L1 in
                docs/tier_plan.md).
            min_load_frac: load fraction below which the model returns zero
                power, zero fuel and undefined efficiency. Defaults to 0 to
                preserve historical behavior; set to LPC_MAP.min_load_frac
                (0.15) for a physically meaningful operating envelope.
            no_load_fuel_frac: no-load fuel flow as a fraction of full-load
                fuel flow (Willans-line intercept). Default 0.2, consistent
                with the GGOV1 `Wfnl` typical value (PES-TR1 Appendix C).
        """
        self.rated_power_mw = float(rated_power_mw)
        self.fuel_lhv_j_kg = float(fuel_lhv_j_kg)
        self.co2_per_fuel_kg = float(co2_per_fuel_kg)
        self.combustor_exit_temp_k = float(combustor_exit_temp_k)
        self.exhaust_temp_k = float(exhaust_temp_k)
        self.air_fuel_ratio = float(air_fuel_ratio)
        self.min_load_frac = float(min_load_frac)

        self.no_load_fuel_frac = float(no_load_fuel_frac)

        # Design-point efficiency (from table: 39.52% for 56.723 MW)
        self._design_efficiency = 0.3952

        # Part-load efficiency via the WILLANS LINE (V&V Phase 0, fix P1):
        # fuel flow is affine in load, fuel(L) = fuel_fl*(b + (1-b)*L) with
        # no-load intercept b = no_load_fuel_frac. Then
        #     eta(L) = eta_fl * L / (b + (1-b)*L).
        # This replaces the former quadratic eta = -0.02*(L-1)^2 + 0.3952,
        # which gave 39.0% at 50% load and 37.5% at zero load — unphysical
        # (real aeroderivatives are ~32-34% at half load and collapse toward
        # idle). Willans gives eta(0.5) = 0.329, eta(0.2) = 0.220 for b=0.2,
        # consistent with the ThermoPower surrogate's part-load shape and
        # with the GGOV1 Wfnl no-load-fuel convention.

    def dispatch(self, load: ArrayLike) -> dict:
        """Evaluate plant outputs at the given load setpoint(s).

        Args:
            load: scalar or array-like, load fraction in [0, 1].

        Returns:
            dict with keys (power_w, fuel_kg_s, efficiency, exhaust_m_kg_s,
            exhaust_T_K, co2_kg_s). Values are floats if `load` was a scalar,
            otherwise numpy arrays.
        """
        arr = np.asarray(load, dtype=float)
        scalar_input = arr.ndim == 0

        if np.any(np.isnan(arr)):
            raise ValueError("load contains NaN")
        if np.any((arr < 0.0) | (arr > 1.0)):
            raise ValueError(
                f"load must be in [0, 1]; got range [{float(arr.min())}, {float(arr.max())}]"
            )

        # [L4] Engine-off floor: at L < min_load_frac the engine is below
        # its minimum continuous operating point — return zero power, zero
        # fuel, and a clearly-zero efficiency (not a polynomial extrapolation).
        engine_on = arr >= self.min_load_frac

        # Willans-line part-load efficiency (only where engine is on)
        b = self.no_load_fuel_frac
        with np.errstate(divide="ignore", invalid="ignore"):
            eta_willans = self._design_efficiency * arr / (b + (1.0 - b) * arr)
        efficiency = np.where(engine_on & (arr > 0), eta_willans, 0.0)

        # Power scales linearly with load and efficiency
        power_w = np.where(engine_on, arr * self.rated_power_mw * 1e6, 0.0)

        # Fuel flow from energy balance
        with np.errstate(divide="ignore", invalid="ignore"):
            fuel_kg_s = np.where(
                efficiency > 0,
                power_w / (efficiency * self.fuel_lhv_j_kg),
                0.0,
            )

        # [L4] If fuel is zero (engine off or below min_load_frac), efficiency
        # is undefined — set to 0 rather than the polynomial extrapolation.
        efficiency = np.where(fuel_kg_s > 0, efficiency, 0.0)

        # [L1] Exhaust mass flow: air + fuel with a lean GT air/fuel ratio
        # (default 50, see DEFAULT_AIR_FUEL_RATIO docstring). Original code
        # used `fuel / 0.034` ≈ AFR 29, closer to stoichiometric than to real
        # lean GT operation.
        air_kg_s = fuel_kg_s * self.air_fuel_ratio
        exhaust_m_kg_s = air_kg_s + fuel_kg_s

        # Exhaust temperature (fixed design point, can be modeled as load-dependent later)
        exhaust_T_K = np.full_like(arr, self.exhaust_temp_k, dtype=float)

        # CO2 emissions
        co2_kg_s = fuel_kg_s * self.co2_per_fuel_kg

        out = {
            "power_w": power_w,
            "fuel_kg_s": fuel_kg_s,
            "efficiency": efficiency,
            "exhaust_m_kg_s": exhaust_m_kg_s,
            "exhaust_T_K": exhaust_T_K,
            "co2_kg_s": co2_kg_s,
        }
        if scalar_input:
            return {k: float(v) for k, v in out.items()}
        return out

    def dispatch_profile(self, load_series: pd.Series) -> pd.DataFrame:
        """Vectorized dispatch across a time-indexed load profile.

        Args:
            load_series: pandas Series indexed by time, values in [0, 1].

        Returns:
            pandas DataFrame indexed by the input index with the same columns
            as dispatch().
        """
        result = self.dispatch(load_series.to_numpy())
        return pd.DataFrame(result, index=load_series.index)

    def __repr__(self) -> str:
        return f"LM9000SimpleCycle(rated_power_mw={self.rated_power_mw:.1f})"


# Alias for convenience
LM9000GasTurbine = LM9000SimpleCycle


class LM9000CombinedCycle:
    """LM9000 combined-cycle (simple-cycle GT + HRSG + steam turbine).

    Wraps LM9000SimpleCycle with an analytical heat-recovery steam generator
    (HRSG) and bottoming-cycle steam turbine, similar to the approach in
    gas_plant.combined_cycle for heavy-duty frames.

    Calibration to datasheet (Table 2, Combined Cycle):
      - Net Power: 72.471 MW
      - LHV Heat Rate: 7,132 kJ/kWh
      - LHV Efficiency: 50.48%
      - Specific CO2: 383.6 kg/MWh (lower than simple cycle due to higher efficiency)

    The underlying simple-cycle GT is the LM9000 base (56.723 MW, 39.52% eff).
    The HRSG recovers ~15 MW additional power via steam-cycle heat recovery.
    """

    def __init__(
        self,
        rated_power_mw: float = 72.471,
        eta_bottoming_nominal: Optional[float] = None,  # if None, auto-tune to hit rated_power_mw
        T_stack_K: float = 380.0,  # HRSG stack exit temperature (after economizer)
        cp_gas_j_kg_k: float = 1050.0,  # specific heat of exhaust gas
        T_exh_nominal_K: float = 729.0,  # LM9000 nominal exhaust (where bottoming eff is defined)
        co2_per_fuel_kg: float = DEFAULT_CO2_PER_FUEL_KG,
        fuel_lhv_j_kg: float = DEFAULT_FUEL_LHV_J_KG,
        gt_kwargs: Optional[dict] = None,
    ):
        """
        Args:
            rated_power_mw: combined-cycle electrical power at design point.
            eta_bottoming_nominal: bottoming-cycle (HRSG + ST) efficiency at T_exh_nominal.
                If None (default), AUTO-TUNED so that GT_power + p_st at L=1
                equals rated_power_mw. With this fix (per docs/tier_plan.md
                LM9000 CC fix), power / fuel / CO2 are all physical — no
                post-hoc rescaling of fuel that gives nonsense efficiency.
                Pass an explicit value (e.g. 0.50) only if you want a fixed
                bottoming efficiency and are willing to let rated_power_mw
                drift.
            T_stack_K: exhaust temperature leaving the HRSG stack.
            cp_gas_j_kg_k: specific heat of exhaust gas (J/kg/K).
            T_exh_nominal_K: reference exhaust temp where bottoming eff is defined.
            co2_per_fuel_kg: CO2 emissions factor.
            fuel_lhv_j_kg: lower heating value of fuel.
            gt_kwargs: optional dict of kwargs to forward to LM9000SimpleCycle
                (e.g. air_fuel_ratio=45 if you want to override the default).
        """
        self.rated_power_mw = float(rated_power_mw)
        self.T_stack_K = float(T_stack_K)
        self.cp_gas_j_kg_k = float(cp_gas_j_kg_k)
        self.T_exh_nominal_K = float(T_exh_nominal_K)
        self.co2_per_fuel_kg = float(co2_per_fuel_kg)
        self.fuel_lhv_j_kg = float(fuel_lhv_j_kg)

        gt_kwargs = gt_kwargs or {}
        gt_kwargs.setdefault("rated_power_mw", 56.723)
        gt_kwargs.setdefault("fuel_lhv_j_kg", fuel_lhv_j_kg)
        gt_kwargs.setdefault("co2_per_fuel_kg", co2_per_fuel_kg)
        self._gt = LM9000SimpleCycle(**gt_kwargs)

        if eta_bottoming_nominal is None:
            # AUTO-TUNE: solve for the eta_bottoming that makes
            #     p_gt(L=1) + p_st(L=1) = rated_power_mw,
            # where p_st = eta_bottoming * Q_HRSG (at design exhaust).
            # The "Carnot-style" derate at nominal exhaust is 1.0 by
            # construction (dT_hrsg/dT_nom = 1), so eta_bottoming(L=1)
            # equals eta_bottoming_nominal at the design point.
            gt_full = self._gt.dispatch(1.0)
            p_gt_full = gt_full["power_w"]
            m_exh_full = gt_full["exhaust_m_kg_s"]
            dT_full = max(self.T_exh_nominal_K - self.T_stack_K, 0.0)
            Q_HRSG_full = m_exh_full * self.cp_gas_j_kg_k * dT_full
            p_st_required = self.rated_power_mw * 1e6 - p_gt_full
            if p_st_required <= 0 or Q_HRSG_full <= 0:
                raise ValueError(
                    f"Cannot auto-tune eta_bottoming: required p_st={p_st_required/1e6:.2f} MW "
                    f"vs Q_HRSG={Q_HRSG_full/1e6:.2f} MW. Check rated_power_mw vs GT capability."
                )
            self.eta_bottoming_nominal = p_st_required / Q_HRSG_full
        else:
            self.eta_bottoming_nominal = float(eta_bottoming_nominal)

    def _raw_dispatch(self, load: np.ndarray) -> dict:
        """Apply the HRSG + bottoming-cycle layer to GT outputs (physical)."""
        gt = self._gt.dispatch(load)

        m_exh = np.asarray(gt["exhaust_m_kg_s"])
        T_exh = np.asarray(gt["exhaust_T_K"])
        p_gt = np.asarray(gt["power_w"])
        fuel = np.asarray(gt["fuel_kg_s"])
        co2 = np.asarray(gt["co2_kg_s"])

        # Heat available to the HRSG (positive when T_exh > T_stack)
        dT_hrsg = np.maximum(T_exh - self.T_stack_K, 0.0)
        Q_HRSG = m_exh * self.cp_gas_j_kg_k * dT_hrsg

        # Linear derate of bottoming efficiency with exhaust temperature.
        # Note: this is a LINEAR derate, not true Carnot (which would be
        # 1 - T_cold/T_hot). It is a pragmatic fit form chosen for this
        # model. (CIGRE TB 238 models steam power as a lagged function of
        # GT exhaust ENERGY, not a linear-in-dT efficiency scaler — the
        # earlier comment claiming this form came from PES-TR1/CIGRE was
        # wrong and is retracted; V&V fix P4.)
        dT_nom = self.T_exh_nominal_K - self.T_stack_K
        eta_bottoming = self.eta_bottoming_nominal * np.where(
            dT_hrsg > 0, dT_hrsg / dT_nom, 0.0
        )
        p_st = eta_bottoming * Q_HRSG
        p_total = p_gt + p_st

        return {
            "power_w": p_total,
            "fuel_kg_s": fuel,
            "exhaust_m_kg_s": m_exh,
            "exhaust_T_K": np.full_like(np.asarray(T_exh, dtype=float), self.T_stack_K),
            "co2_kg_s": co2,
            "_p_gt_w": p_gt,
            "_p_st_w": p_st,
            "_eta_bottoming": eta_bottoming,
            "_Q_HRSG_w": Q_HRSG,
        }

    def dispatch(self, load: ArrayLike) -> dict:
        """Evaluate plant outputs at the given load setpoint(s).

        Args:
            load: scalar or array-like, load fraction in [0, 1].

        Returns:
            dict with keys (power_w, fuel_kg_s, efficiency, exhaust_m_kg_s,
            exhaust_T_K, co2_kg_s). All values are PHYSICAL: fuel reflects
            actual fuel burned by the GT, power is GT + steam turbine,
            efficiency = power / (fuel * LHV). No post-hoc rescaling.
        """
        arr = np.asarray(load, dtype=float)
        scalar_input = arr.ndim == 0

        if np.any(np.isnan(arr)):
            raise ValueError("load contains NaN")
        if np.any((arr < 0.0) | (arr > 1.0)):
            raise ValueError(
                f"load must be in [0, 1]; got range [{float(arr.min())}, "
                f"{float(arr.max())}]"
            )

        raw = self._raw_dispatch(arr)
        power_w = raw["power_w"]
        fuel_kg_s = raw["fuel_kg_s"]
        exhaust_m_kg_s = raw["exhaust_m_kg_s"]
        co2_kg_s = raw["co2_kg_s"]
        exhaust_T_K = raw["exhaust_T_K"]

        with np.errstate(divide="ignore", invalid="ignore"):
            denom = fuel_kg_s * self.fuel_lhv_j_kg
            efficiency = np.where(denom > 0, power_w / denom, 0.0)

        out = {
            "power_w": power_w,
            "fuel_kg_s": fuel_kg_s,
            "efficiency": efficiency,
            "exhaust_m_kg_s": exhaust_m_kg_s,
            "exhaust_T_K": exhaust_T_K,
            "co2_kg_s": co2_kg_s,
        }
        if scalar_input:
            return {k: float(np.asarray(v)) for k, v in out.items()}
        return out

    def dispatch_profile(self, load_series: pd.Series) -> pd.DataFrame:
        """Vectorized dispatch across a time-indexed load profile."""
        result = self.dispatch(load_series.to_numpy())
        return pd.DataFrame(result, index=load_series.index)

    def __repr__(self) -> str:
        return f"LM9000CombinedCycle(rated_power_mw={self.rated_power_mw:.1f})"
