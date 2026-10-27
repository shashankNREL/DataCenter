import math
import numpy as np
from scipy.linalg import expm

out=[]
def P(s): out.append(s)

# ---------- WP2 TRANSFORMER ----------
P("="*70); P("WP2 TRANSFORMER  (2.5 MVA ONAN reference unit)")
P0=2500.0; Pdc=20000.0; Pedd=2000.0; Pstr=1000.0
Ploss_R=P0+Pdc+Pedd+Pstr
Pw_R=Pdc+Pedd                 # winding region share
Po_R=Ploss_R-Pw_R
d_oil_R=45.0; d_grad_R=20.0
G_oa=Ploss_R/d_oil_R
G_wo=Pw_R/d_grad_R
tau_o=10800.0; tau_w=240.0
C_o=G_oa*tau_o; C_w=G_wo*tau_w
kH=(81.0-d_oil_R)/d_grad_R
P(f"Ploss_R={Ploss_R:.1f} W  Pw_R={Pw_R:.1f}  Po_R={Po_R:.1f}")
P(f"G_oa={G_oa:.4f} W/K   G_wo={G_wo:.4f} W/K")
P(f"C_o={C_o:.6e} J/K     C_w={C_w:.6e} J/K")
P(f"k_H={kH:.4f}")
TA=20.0
To_ss=TA+Ploss_R/G_oa; Tw_ss=To_ss+Pw_R/G_wo; TH_ss=To_ss+kH*(Tw_ss-To_ss)
P(f"steady @rated: To={To_ss:.4f} C  Tw={Tw_ss:.4f} C  TH={TH_ss:.4f} C")
A=np.array([[-G_wo/C_w, G_wo/C_w],[G_wo/C_o, -(G_wo+G_oa)/C_o]])
b=np.array([Pw_R/C_w,(Po_R+G_oa*TA)/C_o])
ev=np.linalg.eigvals(A)
P(f"eigenvalues = {ev[0]:.8e}, {ev[1]:.8e}  1/s")
P(f"time constants = {-1/ev[0]:.4f} s, {-1/ev[1]:.4f} s")
xss=np.linalg.solve(-A,b)
P(f"solve check  Tw_ss={xss[0]:.6f}  To_ss={xss[1]:.6f}")
x0=np.array([TA,TA])
for t in [60.,240.,600.,3600.,10800.,43200.]:
    M=expm(A*t); xi=M@x0+np.linalg.solve(A,(M-np.eye(2))@b)
    TH=xi[1]+kH*(xi[0]-xi[1])
    P(f"  t={t:8.0f}s  Tw={xi[0]:9.5f}  To={xi[1]:9.5f}  TH={TH:9.5f}")

P("-"*70); P("harmonic factors, 6-pulse spectrum (pu of fundamental)")
h=np.array([1,5,7,11,13]); Ih=np.array([1.0,0.20,0.14,0.09,0.07])
s2=np.sum(Ih**2); Irms=math.sqrt(s2)
FE=np.sum(h**2*Ih**2)/s2
FS=np.sum(h**0.8*Ih**2)/s2
P(f"I_rms={Irms:.8f} pu   THD={math.sqrt(s2-1)*100:.4f}%")
P(f"F_E (winding eddy) = {FE:.8f}")
P(f"F_S (stray)        = {FS:.8f}")
Kl=Irms
Ploss_h=P0+Kl**2*(Pdc+FE*Pedd+FS*Pstr)
P(f"Ploss with harmonics @K=I_rms: {Ploss_h:.4f} W  (vs {Ploss_R:.1f} sinusoidal)")

P("-"*70); P("paper aging")
B=15000.0; Tref=383.15
def Fage(TC): return math.exp(B*(1/Tref-1/(TC+273.15)))
for TC in [80,95,98,110,120,125,140]:
    P(f"  F_age({TC:3d} C) = {Fage(TC):.8f}")
P(f"2 h @120C -> L_eq = {2*Fage(120):.6f} h")
P(f"180000 h ref: 1 h @120C = {100*Fage(120)/180000:.8e} % ref loss of life")
P("mean-vs-integral (TR-04e): TH = 110 +/- 15 C square wave, 50% duty")
mF=0.5*(Fage(125)+Fage(95)); P(f"  <F(T)> = {mF:.6f}   F(<T>) = {Fage(110):.6f}   ratio = {mF/Fage(110):.4f}")

# ---------- WP4 BATTERY ----------
P("="*70); P("WP4 BATTERY (LFP 100 Ah cell, Ns=240 Np=10)")
Ns,Np=240,10; Q=100.0; Uoc=3.30; R0=6.0e-4; Rp=4.0e-4; Cp=5.0e4
P(f"pack: V={Ns*Uoc:.1f} V  Q={Np*Q:.0f} Ah  E={Ns*Np*Uoc*Q/1000:.3f} kWh")
P(f"R_p*C_p = {Rp*Cp:.4f} s")
Pac=500e3; eta=0.97
Pdc=Pac/eta; Pc=Pdc/(Ns*Np)
disc=Uoc**2-4*R0*Pc
I1=(Uoc-math.sqrt(disc))/(2*R0)
I2=2*Pc/(Uoc+math.sqrt(disc))
P(f"P_ac={Pac/1e3:.0f} kW -> P_dc={Pdc:.3f} W -> P_cell={Pc:.6f} W")
P(f"discriminant={disc:.8f}  I_naive={I1:.8f} A  I_stable={I2:.8f} A  diff={abs(I1-I2):.3e}")
P(f"C-rate = {abs(I2)/Q:.6f} 1/h")
P(f"V_cell = {Uoc-I2*R0:.8f} V (v_p=0)")
Pmax_cell=Uoc**2/(4*R0); P(f"model max cell power = {Pmax_cell:.4f} W -> pack {Pmax_cell*Ns*Np/1e6:.4f} MW (dc, v_p=0)")
P("ECM exact update, I=65.9 A step from v_p=0:")
Ist=65.9
for hh in [0.1,1.0,10.0,20.0,100.0]:
    vp=Rp*Ist*(1-math.exp(-hh/(Rp*Cp)))
    P(f"  h={hh:7.1f}s  v_p={vp:.10f} V")
P("SOC: 50 A for 600 s from z=0.80, Q=100 Ah, eta_Q=1")
dz=50*600/(3600*100); P(f"  dAh={50*600/3600:.6f} Ah  dz={dz:.8f}  z={0.80-dz:.8f}")
P(f"EFC check: 1 full dis+chg of 100Ah -> {2*100*3600/(2*100*3600):.6f}")

# ---------- WP5 FATIGUE ----------
P("="*70); P("WP5 FATIGUE")
Do,Di=0.150,0.050
Wt=math.pi*(Do**4-Di**4)/(16*Do)
P(f"W_t={Wt:.10e} m^3")
for T in [50e3,100e3,200e3]:
    P(f"  T={T/1e3:6.0f} kNm -> tau={T/Wt/1e6:.6f} MPa")
Su=800e6; Ssu=0.6*Su
P(f"S_u={Su/1e6:.0f} MPa  S_su=0.6*S_u={Ssu/1e6:.0f} MPa")
Sa,Sm=100e6,150e6
P(f"Goodman: Sa={Sa/1e6:.0f} Sm={Sm/1e6:.0f} -> Sa_eq={Sa/(1-Sm/Ssu)/1e6:.6f} MPa")
Nref,Saref,m=1e6,200e6,8.0
def Nf(S): return Nref*(Saref/S)**m
for S in [300e6,200e6,150e6,100e6,50e6]:
    P(f"  Basquin Sa={S/1e6:6.1f} MPa -> Nf={Nf(S):.6e}")
P("Haibach: slope beyond knee = 2m-1 = %.1f ; knee at Nk=1e7" % (2*m-1))
Nk=1e7; Sk=Saref*(Nref/Nk)**(1/m)
P(f"  knee amplitude S_k={Sk/1e6:.6f} MPa")
def Nf_h(S):
    if S>=Sk: return Nref*(Saref/S)**m
    return Nk*(Sk/S)**(2*m-1)
P("  damage of 1e9 cycles at 0.5*S_k:")
Spl=0.5*Sk
P(f"    pure Basquin : n/Nf = {1e9/Nf(Spl):.6e}")
P(f"    Haibach      : n/Nf = {1e9/Nf_h(Spl):.6e}")
P(f"    with cutoff  : n/Nf = 0  (ratio basquin/haibach = {Nf_h(Spl)/Nf(Spl):.4f})")

P("-"*70); P("rainflow 4-point, ASTM E1049 sequence [-2,1,-3,5,-1,3,-4,4,-2]")
def rainflow(series):
    st=[];cyc=[]
    for p in series:
        st.append(p)
        while len(st)>=4:
            s1,s2,s3,s4=st[-4],st[-3],st[-2],st[-1]
            r23=abs(s2-s3)
            if r23<=abs(s1-s2) and r23<=abs(s3-s4):
                cyc.append((r23,(s2+s3)/2.0,1.0)); del st[-3:-1]
            else: break
    for i in range(len(st)-1):
        cyc.append((abs(st[i]-st[i+1]),(st[i]+st[i+1])/2.0,0.5))
    return cyc,st
cy,res=rainflow([-2,1,-3,5,-1,3,-4,4,-2])
tot=0.0
for r,mn,c in cy:
    P(f"  range={r:5.2f} mean={mn:6.2f} count={c}"); tot+=c
P(f"  residue={res}  total count={tot}")

# ---------- WP6 CONVERTER ----------
P("="*70); P("WP6 CONVERTER")
R=[0.020,0.030,0.040,0.050]; tau=[5e-4,5e-3,5e-2,5e-1]
P(f"Foster 4-term, sum R = {sum(R):.4f} K/W")
for t in [1e-4,1e-3,1e-2,1e-1,1.0,10.0]:
    z=sum(Ri*(1-math.exp(-t/ti)) for Ri,ti in zip(R,tau))
    P(f"  Z_th({t:8.4g} s) = {z:.10f} K/W")
Von,ron=0.9,2.0e-3; Iav,Irms=120.0,180.0
P(f"conduction: V0={Von} ron={ron} Iavg={Iav} Irms={Irms} -> P={Von*Iav+ron*Irms**2:.4f} W")
fsw=4000.0; Eon,Eoff,Erec=4.2e-3,5.1e-3,2.8e-3
P(f"switching: fsw={fsw} -> P_sw={fsw*(Eon+Eoff+Erec):.4f} W")
P("capacitor Arrhenius: Ea for 10 K halving near 85 C")
T1,T2=358.15,368.15; Rg=8.314
Ea=Rg*math.log(2.0)*T1*T2/(T2-T1)
P(f"  Ea={Ea:.4f} J/mol = {Ea/96485:.6f} eV-equiv")
Lref=1e5; Tcref=358.15; Vref,nc=1.0,5.0
def Lcap(T,Vr): return Lref*math.exp(Ea/Rg*(1/T-1/Tcref))*(Vref/Vr)**nc
for TC in [55,65,75,85,95,105]:
    P(f"  L({TC} C, V=Vref) = {Lcap(TC+273.15,1.0):.4f} h")
P(f"  L(85 C, V=0.8Vref) = {Lcap(358.15,0.8):.4f} h")
P("-"*70); P("WP7 paired-ratio variance demonstration")
rng=np.random.default_rng(20261019)
n=20000
lnK=rng.normal(0,0.8,n)            # shared prefactor uncertainty (ln scale)
lnA=lnK+rng.normal(0,0.05,n); lnB=lnK+rng.normal(0,0.05,n)-0.15
P(f"  sd(ln D_A)            = {np.std(lnA):.6f}")
P(f"  sd(ln D_A - ln D_B)   = {np.std(lnA-lnB):.6f}")
P(f"  variance reduction    = {np.var(lnA)/np.var(lnA-lnB):.3f}x")
P(f"  mean paired log-ratio = {np.mean(lnA-lnB):.6f}  (truth 0.15)")
print("\n".join(out))
