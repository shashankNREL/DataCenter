"""V&V Phase 3 — pin the ANDES-interface assumptions with numeric tests.

The v1 post-processing in gas_plant_andes/scenarios.py relied on two
undocumented assumptions (flagged in the V&V critique):
  1. GENCLS.tm / te are per-unit on the SYSTEM MVA base.
  2. BusFreq devices can be indexed positionally in add-order.
These tests pin both numerically, and exercise the v2 islanded case
(GENROU + TGOV1 + EXST1).

Run:  pixi run pytest tests/test_andes_coupling.py -v
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

andes = pytest.importorskip("andes")

from gas_plant import GasTurbinePlant  # noqa: E402
from gas_plant_andes.case_builder import (  # noqa: E402
    IslandedCaseConfig, build_islanded_case_v2,
)


def _small_gencls_case(p_mw=15.0, sn_mva=23.0, sys_mva=100.0):
    andes.config_logger(stream_level=50)
    ss = andes.System()
    ss.config.mva = sys_mva
    ss.config.freq = 60.0
    ss.add('Bus', dict(idx='B1', Vn=20.0))
    ss.add('Bus', dict(idx='B2', Vn=20.0))
    ss.add('Line', dict(idx='LN', bus1='B1', bus2='B2', r=0.0, x=0.01))
    ss.add('Slack', dict(idx='G1', bus='B1', p0=p_mw / sys_mva, v0=1.0, Vn=20.0))
    ss.add('PQ', dict(idx='L1', bus='B2', p0=p_mw / sys_mva, q0=0.0, Vn=20.0))
    ss.add('GENCLS', dict(idx='SYN1', bus='B1', gen='G1', Sn=sn_mva, Vn=20.0,
                          M=5.6, D=0.0, ra=0.0, xd1=0.2))
    ss.add('BusFreq', dict(idx='F_B1', bus='B1'))
    ss.add('BusFreq', dict(idx='F_B2', bus='B2'))
    ss.setup()
    ss.PFlow.run()
    assert ss.PFlow.converged
    return ss


class TestAndesBaseAssumptions:
    def test_tm_is_on_system_base(self):
        """15 MW dispatch on a lossless line: tm0 must be 0.15 pu on the
        100 MVA SYSTEM base (0.652 pu if it were machine base)."""
        ss = _small_gencls_case()
        # tm is a TDS variable — it is initialized at TDS init, not PFlow
        ss.TDS.init()
        tm0 = float(ss.GENCLS.tm.v[0])
        assert tm0 == pytest.approx(0.15, abs=0.002)
        assert abs(tm0 - 15.0 / 23.0) > 0.4  # clearly NOT machine base

    def test_busfreq_indexing_by_idx(self):
        """Resolve BusFreq by idx, not position, and verify both exist."""
        ss = _small_gencls_case()
        uid = ss.BusFreq.idx2uid('F_B2')
        assert ss.BusFreq.bus.v[uid] == 'B2'

    def test_governor_pout_enters_tm_directly(self):
        """TurbineGov contributes (pout - tm0) to the tm equation: after
        setup, TGOV1.pout initial equals GENCLS.tm0 (both system base)."""
        ss = _small_gencls_case()
        # add TGOV1 requires re-building the system; do it inline
        ss2 = andes.System()
        ss2.config.mva = 100.0
        ss2.config.freq = 60.0
        ss2.add('Bus', dict(idx='B1', Vn=20.0))
        ss2.add('Bus', dict(idx='B2', Vn=20.0))
        ss2.add('Line', dict(idx='LN', bus1='B1', bus2='B2', r=0.0, x=0.01))
        ss2.add('Slack', dict(idx='G1', bus='B1', p0=0.15, v0=1.0, Vn=20.0))
        ss2.add('PQ', dict(idx='L1', bus='B2', p0=0.15, q0=0.0, Vn=20.0))
        ss2.add('GENCLS', dict(idx='SYN1', bus='B1', gen='G1', Sn=23.0,
                               Vn=20.0, M=5.6, D=0.0, ra=0.0, xd1=0.2))
        ss2.add('TGOV1', dict(idx='TG1', syn='SYN1', R=0.04, T1=0.15, T2=0.3,
                              T3=1.5, Dt=0.0, VMAX=0.9565, VMIN=0.15))
        ss2.setup()
        ss2.PFlow.run()
        ss2.TDS.config.tf = 0.1
        ss2.TDS.config.no_tqdm = 1
        ss2.TDS.run()
        pout0 = float(ss2.TGOV1.pout.v[0])
        tm0 = float(ss2.GENCLS.tm.v[0])
        assert pout0 == pytest.approx(tm0, abs=1e-6)


class TestIslandedCaseV2:
    def test_builds_and_islands(self):
        cfg = IslandedCaseConfig(
            plant=GasTurbinePlant(rated_power_mw=235.0),
            plant_load_setpoint=0.85,
            data_center_mw=180.0,
            data_center_mvar=30.0,
            island_time_s=2.0,
            resync_time_s=None,
        )
        ss = build_islanded_case_v2(cfg)
        # Sn = MW / 0.85
        assert float(ss.GENROU.Sn.v[0]) == pytest.approx(235.0 / 0.85, rel=1e-6)
        # exciter present and attached
        assert ss.EXST1.n == 1
        # Governor VMAX is entered as PF (= turbine MW on machine base);
        # ANDES converts governor power parameters to SYSTEM base at setup:
        # VMAX_sys = PF * Sn / mva_sys. Pinning this conversion is the point.
        sn = 235.0 / 0.85
        assert float(ss.TGOV1.VMAX.v[0]) == pytest.approx(0.85 * sn / 100.0,
                                                          rel=1e-6)
        ss.TDS.config.tf = 6.0
        ss.TDS.config.tstep = 0.01
        ss.TDS.config.no_tqdm = 1
        ss.TDS.run()
        assert ss.TDS.converged
        omega = np.asarray(ss.dae.ts.x)[:, ss.GENROU.omega.a[0]]
        # islanding a net-importing bus: machine must move away from 1 pu
        assert abs(omega - 1.0).max() > 1e-4

    def test_reclose_guard_warns_and_omits_toggle(self):
        cfg = IslandedCaseConfig(
            plant=GasTurbinePlant(rated_power_mw=235.0),
            plant_load_setpoint=0.85,
            data_center_mw=180.0,
            island_time_s=2.0,
            resync_time_s=15.0,
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ss = build_islanded_case_v2(cfg)
            assert any("sync-check" in str(x.message) for x in w)
        assert ss.Toggle.n == 1  # only the islanding event
