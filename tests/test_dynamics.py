"""V&V invariant + regression tests for the dynamics package.

Phase 1 (invariants): every test encodes a physical or structural identity
the models must satisfy regardless of parameter tuning.
Phase 4 (regressions): pinned numbers from the post-fix models so future
edits cannot silently change behavior.

Run:  pixi run test   (= pytest tests/test_dynamics.py -v)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gas_plant.dynamics.ggov1 import (  # noqa: E402
    GGOV1Params, _initial_state_for_load, _pref_for_rselect, _rhs_factory,
    simulate_ggov1, step_response,
)
from gas_plant.dynamics.tgov1 import TGOV1Params, simulate_tgov1  # noqa: E402
from gas_plant.dynamics.multishaft import (  # noqa: E402
    MultishaftParams, simulate_multishaft,
    _initial_state_for_load as ms_ic,
    _rhs_factory as ms_rhs_factory,
)
from gas_plant.dynamics.torsional import TorsionalParams  # noqa: E402
from gas_plant.lm9000 import LM9000SimpleCycle, LM9000CombinedCycle  # noqa: E402


LM = GGOV1Params.lm2500_overrides


# ---------------------------------------------------------------------------
# GGOV1 structural / physical invariants
# ---------------------------------------------------------------------------

class TestGGOV1Invariants:
    def test_ic_is_equilibrium(self):
        p = LM()
        for pe_mw in [5.0, 15.0, 21.9]:
            pe_turb = pe_mw / p.Sn_mva / p.kb
            st = _initial_state_for_load(pe_turb, p)
            rhs = _rhs_factory(p, _pref_for_rselect(pe_turb, p), pe_mw / p.Sn_mva)
            assert np.abs(rhs(0.0, st.as_array())).max() < 1e-9

    def test_linearized_stability(self):
        p = LM()
        pe_turb = 15.0 / p.Sn_mva / p.kb
        st = _initial_state_for_load(pe_turb, p)
        rhs = _rhs_factory(p, _pref_for_rselect(pe_turb, p), 15.0 / p.Sn_mva)
        y0 = st.as_array()
        n = y0.size
        J = np.zeros((n, n))
        f0 = rhs(0.0, y0)
        for i in range(n):
            dy = np.zeros(n)
            dy[i] = 1e-7 * max(1.0, abs(y0[i]))
            J[:, i] = (rhs(0.0, y0 + dy) - f0) / dy[i]
        eig = np.linalg.eigvals(J)
        # exactly one structural zero mode (rotor angle), rest strictly stable
        assert int(np.sum(np.abs(eig.real) < 1e-9)) == 1
        assert max(e.real for e in eig if abs(e.real) >= 1e-9) < -1e-3

    def test_steady_hold_no_drift(self):
        res = simulate_ggov1(np.array([0.0, 300.0]), np.array([15.0, 15.0]),
                             params=LM(), sample_dt_s=1.0)
        assert abs(res.freq_hz - 60.0).max() * 1000 < 0.1  # < 0.1 mHz
        assert abs(res.Pm_mw[-1] - 15.0) < 1e-3

    def test_droop_identity(self):
        p = LM()
        res = simulate_ggov1(np.array([0.0, 5.0, 200.0]),
                             np.array([11.5, 14.0, 14.0]),
                             params=p, sample_dt_s=0.5)
        dw = res.omega_pu[-1] - 1.0
        dPe_turb = (res.Pe_mw[-1] - res.Pe_mw[0]) / p.Trate_mw
        assert abs(-dw / dPe_turb - p.R) / p.R < 0.01

    def test_isochronous_returns_to_60(self):
        res = simulate_ggov1(np.array([0.0, 5.0, 200.0]),
                             np.array([11.5, 14.0, 14.0]),
                             params=LM(rselect=0), sample_dt_s=0.5)
        assert abs(res.freq_hz[-1] - 60.0) * 1000 < 0.1

    def test_thermal_cap_enforced_at_22mw(self):
        """Demand above Ldref*Trate: temp limiter must wind Pm back to 22 MW."""
        p = LM()
        res = step_response(15.0, 25.0, t_end_s=200.0, sample_dt_s=0.5, params=p)
        assert abs(res.Pm_mw[-1] - p.Ldref * p.Trate_mw) < 0.05
        # and the temperature limiter must be the selected controller at the end
        assert res.fsrt_pu[-1] == pytest.approx(res.fsr_pu[-1], abs=1e-6)

    def test_transient_ceiling(self):
        p = LM()
        res = step_response(15.0, 25.0, t_end_s=200.0, sample_dt_s=0.1, params=p)
        assert res.Pm_mw.max() <= p.Pm_transient_max_pu * p.Trate_mw + 0.05

    def test_initial_load_guard(self):
        with pytest.raises(ValueError):
            simulate_ggov1(np.array([0.0, 10.0]), np.array([23.0, 23.0]),
                           params=LM())

    def test_dm_positive_damps_overspeed(self):
        r0 = step_response(18.0, 5.0, t_end_s=30.0, params=LM())
        r1 = step_response(18.0, 5.0, t_end_s=30.0, params=LM(Dm=0.5))
        assert r1.freq_hz.max() < r0.freq_hz.max() - 0.5

    def test_kbc_sensitivity(self):
        """Back-calc anti-windup gain is an implementation knob; results must
        be insensitive across a 25x range (measured spread: 2.1 mHz)."""
        nadirs = []
        for scale in [0.2, 1.0, 5.0]:
            p = LM(Kbc_speed=100 * scale, Kbc_accel=100 * scale,
                   Kbc_temp=50 * scale)
            r = step_response(11.5, 13.8, t_end_s=20.0, params=p)
            nadirs.append(r.freq_hz.min())
        assert max(nadirs) - min(nadirs) < 0.005  # 5 mHz

    def test_fuel_calibration_at_rated(self):
        p = LM()
        res = simulate_ggov1(np.array([0.0, 400.0]), np.array([22.0, 22.0]),
                             params=p, sample_dt_s=1.0)
        fuel_expected = p.Trate_mw * 1e6 / (p.eta_design * p.fuel_lhv_j_kg)
        assert res.fuel_kg_s[-1] == pytest.approx(fuel_expected, rel=1e-3)

    def test_rselect_variants_run_and_hold(self):
        for rsel in [1, 0, -1, -2]:
            res = simulate_ggov1(np.array([0.0, 30.0]), np.array([15.0, 15.0]),
                                 params=LM(rselect=rsel), sample_dt_s=1.0)
            assert abs(res.freq_hz - 60.0).max() * 1000 < 0.1, f"rselect={rsel}"


# ---------------------------------------------------------------------------
# TGOV1 invariants
# ---------------------------------------------------------------------------

class TestTGOV1Invariants:
    def test_droop_identity(self):
        p = TGOV1Params()
        res = simulate_tgov1(np.array([0.0, 5.0, 120.0]),
                             np.array([15.0, 18.0, 18.0]),
                             params=p, sample_dt_s=0.5)
        dw = res.omega_pu[-1] - 1.0
        dPe = (res.Pe_mw[-1] - res.Pe_mw[0]) / p.Sn_mva
        assert abs(-dw / dPe - p.R_droop) / p.R_droop < 0.01

    def test_vmax_caps_power(self):
        p = TGOV1Params()
        res = simulate_tgov1(np.array([0.0, 5.0, 120.0]),
                             np.array([15.0, 21.5, 21.5]),
                             params=p, sample_dt_s=0.5)
        # Non-windup limiter in continuous ODE form: RK45 internal stages can
        # overshoot the limit by O(1e-4) before the derivative zeroing takes
        # effect; allow that integration-scale tolerance.
        assert res.valve_pu.max() <= p.vmax_pu + 5e-4

    def test_steady_hold(self):
        res = simulate_tgov1(np.array([0.0, 300.0]), np.array([15.0, 15.0]),
                             sample_dt_s=1.0)
        assert abs(res.freq_hz - 60.0).max() * 1000 < 0.1


# ---------------------------------------------------------------------------
# Multishaft (Tier C) invariants
# ---------------------------------------------------------------------------

class TestMultishaftInvariants:
    def test_ic_is_equilibrium(self):
        p = MultishaftParams()
        g = p.ggov1
        pe_turb = 15.0 / g.Sn_mva / g.kb
        st = ms_ic(pe_turb, p)
        rhs = ms_rhs_factory(p, _pref_for_rselect(pe_turb, g), 15.0 / g.Sn_mva)
        assert np.abs(rhs(0.0, st.as_array())).max() < 1e-9

    def test_hp_rotor_energy_balance(self):
        """Power form: M_hp * (w_end - w_start) = integral of (Pm_gov - P_couple)."""
        p = MultishaftParams()
        res = simulate_multishaft(np.array([0.0, 2.0, 40.0]),
                                  np.array([15.0, 18.0, 18.0]),
                                  params=p, sample_dt_s=0.01)
        g = p.ggov1
        net_pu = (res.Pm_hp_mw - res.P_couple_mw) / g.Trate_mw
        lhs = p.M_hp * (res.omega_hp_pu[-1] - res.omega_hp_pu[0])
        rhs_ = np.trapezoid(net_pu, res.t_s)
        assert abs(lhs - rhs_) < 1e-3

    def test_hp_speed_maps_to_load(self):
        """SS NGG speed must equal idle + load_frac*(full-idle)."""
        p = MultishaftParams()
        res = simulate_multishaft(np.array([0.0, 60.0]), np.array([15.0, 15.0]),
                                  params=p, sample_dt_s=1.0)
        g = p.ggov1
        load_frac_turb = 15.0 / g.Sn_mva / g.kb  # pu of 22 MW
        omega_hp_expect = p.omega_hp_idle + load_frac_turb / p.K_couple
        assert res.omega_hp_pu[-1] == pytest.approx(omega_hp_expect, abs=1e-4)

    def test_fuel_matches_ggov1(self):
        """Same governor, same load: Tier B and Tier C fuel must agree at SS."""
        gp = LM()
        rb = simulate_ggov1(np.array([0.0, 60.0]), np.array([15.0, 15.0]),
                            params=gp, sample_dt_s=1.0)
        rc = simulate_multishaft(np.array([0.0, 60.0]), np.array([15.0, 15.0]),
                                 params=MultishaftParams(ggov1=gp), sample_dt_s=1.0)
        assert rc.fuel_kg_s[-1] == pytest.approx(rb.fuel_kg_s[-1], rel=1e-4)


# ---------------------------------------------------------------------------
# Torsional invariants
# ---------------------------------------------------------------------------

class TestTorsionalInvariants:
    def test_natural_frequency_matches_design(self):
        tp = TorsionalParams()
        M_pt, M_gen = 2 * tp.H_pt_only_s, 2 * tp.H_gen_s
        I_red = M_pt * M_gen / (M_pt + M_gen)
        f_actual = np.sqrt(tp.omega_0_rad_s * tp.k_pu / I_red) / (2 * np.pi)
        assert f_actual == pytest.approx(tp.f_torsion_hz, abs=1e-6)

    def test_damping_ratio(self):
        tp = TorsionalParams()
        M_pt, M_gen = 2 * tp.H_pt_only_s, 2 * tp.H_gen_s
        I_red = M_pt * M_gen / (M_pt + M_gen)
        omega_n = 2 * np.pi * tp.f_torsion_hz
        # c = 2*zeta*omega_n*I_red (pu) -> recovered zeta must match
        zeta = tp.c_pu / (2 * omega_n * I_red)
        assert zeta == pytest.approx(tp.zeta_torsion, rel=1e-9)


# ---------------------------------------------------------------------------
# LM9000 (Willans line) invariants
# ---------------------------------------------------------------------------

class TestLM9000:
    def test_willans_efficiency_values(self):
        lm = LM9000SimpleCycle()
        b = lm.no_load_fuel_frac
        for L in [0.2, 0.5, 0.8, 1.0]:
            eta = lm.dispatch(L)["efficiency"]
            assert eta == pytest.approx(0.3952 * L / (b + (1 - b) * L), rel=1e-9)

    def test_fuel_affine_in_load(self):
        """Willans line <=> fuel flow affine in load."""
        lm = LM9000SimpleCycle()
        L = np.array([0.25, 0.5, 0.75, 1.0])
        fuel = lm.dispatch(L)["fuel_kg_s"]
        slopes = np.diff(fuel) / np.diff(L)
        assert np.allclose(slopes, slopes[0], rtol=1e-9)

    def test_energy_balance_first_law(self):
        """power <= fuel * LHV at every load (first law)."""
        lm = LM9000SimpleCycle()
        L = np.linspace(0.05, 1.0, 20)
        d = lm.dispatch(L)
        assert np.all(d["power_w"] <= d["fuel_kg_s"] * lm.fuel_lhv_j_kg + 1e-6)

    def test_cc_design_point_preserved(self):
        cc = LM9000CombinedCycle()
        d = cc.dispatch(1.0)
        assert d["power_w"] / 1e6 == pytest.approx(72.471, rel=1e-3)
        assert d["efficiency"] == pytest.approx(0.5048, rel=2e-3)


# ---------------------------------------------------------------------------
# Phase 4 regression pins (post-fix baselines, established 2026-07-18)
# ---------------------------------------------------------------------------

class TestRegressionBaselines:
    def test_ggov1_step_nadir(self):
        r = step_response(11.5, 13.8, t_end_s=20.0, params=LM())
        assert r.freq_hz.min() == pytest.approx(59.450, abs=0.005)

    def test_ggov1_step_final(self):
        r = step_response(11.5, 13.8, t_end_s=60.0, params=LM())
        assert r.freq_hz[-1] == pytest.approx(59.758, abs=0.005)

    def test_tgov1_step_nadir_and_final(self):
        r = simulate_tgov1(np.array([0.0, 5.0, 60.0]),
                           np.array([15.0, 18.0, 18.0]), sample_dt_s=0.1)
        assert r.freq_hz.min() == pytest.approx(59.300, abs=0.005)
        assert r.freq_hz[-1] == pytest.approx(59.701, abs=0.005)

    def test_multishaft_step_nadir(self):
        r = simulate_multishaft(np.array([0.0, 2.0, 40.0]),
                                np.array([15.0, 18.0, 18.0]),
                                sample_dt_s=0.1)
        assert r.freq_hz.min() == pytest.approx(58.772, abs=0.01)

    def test_ggov1_fuel_at_15mw(self):
        r = simulate_ggov1(np.array([0.0, 60.0]), np.array([15.0, 15.0]),
                           params=LM(), sample_dt_s=1.0)
        assert r.fuel_kg_s[-1] == pytest.approx(0.9290, abs=0.002)

    def test_load17_10min_replay(self):
        """First 10 minutes of the Load17 data-center trace (x10 MW scaling
        per the notebooks). Pins |df|_max and cumulative fuel."""
        load17 = Path(__file__).resolve().parents[1] / "data" / "load17.csv"
        import pandas as pd
        l = pd.read_csv(load17, sep=r"\s+", header=None, names=["t", "d"])
        t = l.t.values.astype(float)
        mw = l.d.values * 10.0
        m = t <= 600.0
        r = simulate_ggov1(t[m], mw[m], params=LM(), sample_dt_s=1.0)
        assert abs(r.freq_hz - 60.0).max() * 1000 == pytest.approx(228.3, abs=2.0)
        assert r.cum_fuel_kg[-1] == pytest.approx(640.1, abs=1.0)
