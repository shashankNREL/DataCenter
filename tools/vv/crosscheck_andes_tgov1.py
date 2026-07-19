"""V&V Phase 1 — cross-simulate scipy TGOV1 vs ANDES TGOV1.

Builds a single-machine islanded ANDES case (GENCLS + TGOV1, constant-power
load) and the equivalent scipy `simulate_tgov1` case, applies the same load
step, and compares rotor speed and mechanical power trajectories.

Matching conditions:
  - scipy model: alpha_load_damping = 0 (ANDES PQ set to constant power),
    D_pu identical, rate limit off (standard TGOV1 has none).
  - ANDES: PQ p2p = 1 (no Z/I conversion in TDS), GENCLS on machine base
    Sn = 23 MVA, TGOV1 on the machine base.
  - ANDES GENCLS uses the speed-voltage approximation (stator flux at
    w = 1): te = psid*Iq - psiq*Id = Pe for ra = 0, and the governor pout
    enters the tm equation one-to-one (TurbineGov adds pout - tm0). ANDES's
    effective swing equation is therefore the POWER form; the scipy model
    runs in its default power form. Verified against GENBase/GENCLS source.

Run:  pixi run python tools/vv/crosscheck_andes_tgov1.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import andes  # noqa: E402
from gas_plant.dynamics.tgov1 import TGOV1Params, simulate_tgov1  # noqa: E402

# ---- Shared scenario ----
SN_MVA = 23.0
H_S = 2.8
D = 0.0
R = 0.04
T1, T2, T3 = 0.15, 0.30, 1.50
VMAX, VMIN = 22.0 / 23.0, 0.15
P0_MW, P1_MW = 15.0, 18.0
T_STEP = 1.0
T_END = 30.0
SYS_MVA = 100.0


def run_andes() -> dict:
    andes.config_logger(stream_level=50)
    ss = andes.System()
    ss.config.mva = SYS_MVA
    ss.config.freq = 60.0

    # NOTE: a degenerate single-bus case leaves GENCLS.omega frozen at 1.0
    # in ANDES 2.0 (network equations collapse) — a 2-bus case is required.
    # The line is LOSSLESS (r=0) so the machine sees exactly the PQ demand;
    # with r=0.001 the extra I^2*r loss (≈33 kW at the stepped load) shows
    # up as a 3.4 mHz steady-state frequency offset vs the scipy case.
    ss.add('Bus', dict(idx='B1', name='B1', Vn=20.0))
    ss.add('Bus', dict(idx='B2', name='B2', Vn=20.0))
    ss.add('Line', dict(idx='LN', bus1='B1', bus2='B2', r=0.0, x=0.01))
    ss.add('Slack', dict(idx='G1', bus='B1', p0=P0_MW / SYS_MVA, v0=1.0, Vn=20.0))
    ss.add('PQ', dict(idx='L1', bus='B2', p0=P0_MW / SYS_MVA, q0=0.0, Vn=20.0))
    ss.add('GENCLS', dict(idx='SYN1', bus='B1', gen='G1',
                          Sn=SN_MVA, Vn=20.0, M=2 * H_S, D=D, ra=0.0, xd1=0.2))
    ss.add('TGOV1', dict(idx='TG1', syn='SYN1',
                         R=R, T1=T1, T2=T2, T3=T3, Dt=0.0,
                         VMAX=VMAX, VMIN=VMIN))
    ss.add('Alter', dict(model='PQ', dev='L1', src='Ppf', attr='v',
                         t=T_STEP, method='=', amount=P1_MW / SYS_MVA))

    # Constant-power load in TDS (no conversion to impedance)
    ss.setup()
    ss.PQ.config.p2p = 1.0
    ss.PQ.config.p2i = 0.0
    ss.PQ.config.p2z = 0.0
    ss.PQ.config.q2q = 1.0
    ss.PQ.config.q2i = 0.0
    ss.PQ.config.q2z = 0.0

    ss.PFlow.run()
    assert ss.PFlow.converged

    ss.TDS.config.tf = T_END
    ss.TDS.config.tstep = 0.01
    ss.TDS.config.no_tqdm = 1
    ss.TDS.run()
    assert ss.TDS.converged

    t = np.asarray(ss.dae.ts.t)
    x = np.asarray(ss.dae.ts.x)
    y = np.asarray(ss.dae.ts.y)
    omega = x[:, ss.GENCLS.omega.a[0]]
    # GENCLS.tm is on the SYSTEM MVA base (verified empirically: tm0 =
    # 0.1507 pu on 100 MVA for the 15 MW + losses dispatch). ANDES passes
    # the governor output pout into tm DIRECTLY (torque = power at the
    # interface); compare it against the scipy governor output Pm.
    tm = y[:, ss.GENCLS.tm.a[0]]
    return {"t": t, "omega": omega, "pm_mw": tm * SYS_MVA}


def run_scipy() -> dict:
    p = TGOV1Params(
        Sn_mva=SN_MVA, H_s=H_S, D_pu=D,
        R_droop=R, T1_s=T1, T2_s=T2, T3_s=T3, Dt_pu=0.0,
        vmax_pu=VMAX, vmin_pu=VMIN,
        use_valve_rate_limit=False,
        alpha_load_damping=0.0,   # match ANDES constant-P load
        # power form matches ANDES GENCLS exactly: with the speed-voltage
        # approximation (flux at w=1) and ra=0, te = psid*Iq - psiq*Id = Pe,
        # and the governor pout enters tm one-to-one.
        torque_form=False,
        rtol=1e-8, atol=1e-10,
    )
    res = simulate_tgov1(
        load_time_s=np.array([0.0, T_STEP, T_END]),
        load_demand_mw=np.array([P0_MW, P1_MW, P1_MW]),
        params=p, sample_dt_s=0.01,
    )
    return {"t": res.t_s, "omega": res.omega_pu, "pm_mw": res.Pm_mw}


def main() -> int:
    a = run_andes()
    s = run_scipy()

    # Interpolate ANDES onto the scipy grid
    omega_a = np.interp(s["t"], a["t"], a["omega"])
    pm_a = np.interp(s["t"], a["t"], a["pm_mw"])

    domega = np.abs(omega_a - s["omega"])
    dpm = np.abs(pm_a - s["pm_mw"])

    nadir_a = 60.0 * omega_a.min()
    nadir_s = 60.0 * s["omega"].min()

    print("=== TGOV1 cross-check: scipy vs ANDES ===")
    print(f"max |domega|      : {domega.max():.3e} pu "
          f"({domega.max() * 60 * 1000:.3f} mHz)")
    print(f"max |dPm|         : {dpm.max():.4f} MW "
          f"({dpm.max() / SN_MVA:.3e} pu)")
    print(f"freq nadir  ANDES : {nadir_a:.4f} Hz")
    print(f"freq nadir  scipy : {nadir_s:.4f} Hz")
    print(f"final f     ANDES : {60 * omega_a[-1]:.4f} Hz")
    print(f"final f     scipy : {60 * s['omega'][-1]:.4f} Hz")

    # Acceptance: with matched formulations the two implementations agree to
    # solver tolerance (measured 0.12 mHz / 8.6e-5 pu); gate with headroom.
    ok = (domega.max() * 60 * 1000 < 1.0) and (dpm.max() / SN_MVA < 1e-3)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
