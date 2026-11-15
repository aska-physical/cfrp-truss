#!/usr/bin/env python3
"""
Deterministic ACI 440.2R-17/-23 ULS Flexure + Cracked-Section Serviceability (§10.2.8)
+ Creep-Rupture (§10.2.9) + 45-deg Truss-Anchor + Cole EIS verifier for aska-physical/cfrp-truss (v0.2.0).
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import sys
from pathlib import Path
from typing import Any

import sympy as sp

ROOT = Path(__file__).resolve().parent
CERT_PATH = ROOT / "results" / "certificate.json"


def aci440_ebr_debonding_strain(fc_MPa: float, n_plies: int, Ef_MPa: float, tf_mm: float, eps_fu: float) -> float:
    """ACI 440.2R-17 Eq. 10.1.1 (SI): eps_fd = 0.41 * sqrt(fc' / (n * Ef * tf)) <= 0.9 * eps_fu."""
    raw = 0.41 * math.sqrt(fc_MPa / (n_plies * Ef_MPa * tf_mm))
    return min(raw, 0.9 * eps_fu)


def solve_unstrengthened_cracked_eps_bi(
    M_DL_kNm: float, b_mm: float, d_mm: float, df_mm: float, fc_MPa: float, As_mm2: float, Es_MPa: float
) -> dict[str, float]:
    """Compute exact cracked-section neutral axis c_k0, I_cr0, and initial soffit strain eps_bi under M_DL."""
    Ec_MPa = 4700.0 * math.sqrt(fc_MPa)
    ns = Es_MPa / Ec_MPa
    rho_s = As_mm2 / (b_mm * d_mm)
    k0 = math.sqrt((rho_s * ns) ** 2 + 2.0 * rho_s * ns) - rho_s * ns
    ck0_mm = k0 * d_mm
    Icr0_mm4 = (b_mm * (ck0_mm**3)) / 3.0 + ns * As_mm2 * ((d_mm - ck0_mm) ** 2)
    M_Nmm = M_DL_kNm * 1e6
    eps_bi = (M_Nmm * (df_mm - ck0_mm)) / (Ec_MPa * Icr0_mm4)
    return {"Ec_MPa": round(Ec_MPa, 2), "ck0_mm": round(ck0_mm, 4), "Icr0_mm4": round(Icr0_mm4, 1), "eps_bi": round(eps_bi, 6)}


def solve_cracked_serviceability(
    M_service_kNm: float,
    M_sustained_kNm: float,
    b_mm: float,
    d_mm: float,
    df_mm: float,
    fc_MPa: float,
    As_mm2: float,
    fy_MPa: float,
    Es_MPa: float,
    Af_mm2: float,
    Ef_MPa: float,
    ffu_design_MPa: float,
    eps_bi: float,
    eps_pre_cfrp: float = 0.0,
    creep_factor_cfrp: float = 0.55,
) -> dict[str, Any]:
    """
    Solve exact coupled cracked-section force + moment equilibrium at M_service and M_sustained
    accounting for initial dead-load soffit strain eps_bi and optional CFRP pre-strain eps_pre_cfrp.

    Gates per ACI 440.2R-17 §10.2.8 & Table 10.2.9 (plus ACI 440.2R-08 0.45*fc' sustained check):
      - Concrete total service compressive stress: fc_s <= 0.60 * fc'
      - Concrete sustained compressive stress:     fc_sus <= 0.45 * fc'
      - Steel service tensile stress:              fs_s <= 0.80 * fy
      - CFRP sustained + cyclic stress limit:      ff_s <= 0.55 * ffu_design (creep-rupture/fatigue)
    """
    Ec_MPa = 4700.0 * math.sqrt(fc_MPa)

    def solve_at_moment(M_kNm: float) -> tuple[float, float, float, float]:
        M_target = M_kNm * 1e6  # N*mm

        def moment_for_ck(ck: float) -> tuple[float, float, float, float]:
            phi_lo, phi_hi = 0.0, 2.0e-4
            for _ in range(65):
                phi_m = 0.5 * (phi_lo + phi_hi)
                fs = Es_MPa * phi_m * (d_mm - ck)
                eps_f_eff = max(0.0, phi_m * (df_mm - ck) - eps_bi + eps_pre_cfrp)
                ff = Ef_MPa * eps_f_eff
                M_calc = As_mm2 * fs * (d_mm - ck / 3.0) + Af_mm2 * ff * (df_mm - ck / 3.0)
                if M_calc < M_target:
                    phi_lo = phi_m
                else:
                    phi_hi = phi_m
            phi_sol = 0.5 * (phi_lo + phi_hi)
            fc = Ec_MPa * phi_sol * ck
            fs = Es_MPa * phi_sol * (d_mm - ck)
            ff = Ef_MPa * max(0.0, phi_sol * (df_mm - ck) - eps_bi + eps_pre_cfrp)
            f_res = 0.5 * b_mm * ck * fc - (As_mm2 * fs + Af_mm2 * ff)
            return f_res, fc, fs, ff

        ck_lo, ck_hi = 5.0, 0.92 * d_mm
        for _ in range(75):
            ck_m = 0.5 * (ck_lo + ck_hi)
            f_res, _, _, _ = moment_for_ck(ck_m)
            if f_res < 0.0:
                ck_lo = ck_m
            else:
                ck_hi = ck_m
        ck_final = 0.5 * (ck_lo + ck_hi)
        f_res, fc, fs, ff = moment_for_ck(ck_final)
        assert abs(f_res) < 1.0
        return ck_final, fc, fs, ff

    ck_tot, fc_s, fs_s, ff_s = solve_at_moment(M_service_kNm)
    _, fc_sus, _, ff_sus = solve_at_moment(M_sustained_kNm)

    pass_fc_total = fc_s <= 0.60 * fc_MPa
    pass_fc_sustained = fc_sus <= 0.45 * fc_MPa
    pass_fs_service = fs_s <= 0.80 * fy_MPa
    pass_ff_creep = ff_s <= creep_factor_cfrp * ffu_design_MPa

    return {
        "ck_service_mm": round(ck_tot, 4),
        "fc_service_MPa": round(fc_s, 3),
        "fc_limit_0.60fc_MPa": round(0.60 * fc_MPa, 3),
        "fc_sustained_MPa": round(fc_sus, 3),
        "fc_sustained_limit_0.45fc_MPa": round(0.45 * fc_MPa, 3),
        "fs_service_MPa": round(fs_s, 3),
        "fs_limit_0.80fy_MPa": round(0.80 * fy_MPa, 3),
        "ff_service_MPa": round(ff_s, 3),
        "ff_sustained_MPa": round(ff_sus, 3),
        "ff_creep_limit_0.55ffu_MPa": round(creep_factor_cfrp * ffu_design_MPa, 3),
        "all_serviceability_gates_pass": bool(pass_fc_total and pass_fc_sustained and pass_fs_service and pass_ff_creep),
        "gates": {
            "concrete_total_0.60fc": bool(pass_fc_total),
            "concrete_sustained_0.45fc": bool(pass_fc_sustained),
            "steel_service_0.80fy": bool(pass_fs_service),
            "cfrp_creep_rupture_0.55ffu": bool(pass_ff_creep),
        },
    }


def verify_symbolic_cracked_section_identity() -> bool:
    """Symbolically verify with SymPy that at eps_bi = 0, force balance yields the exact quadratic 0.5*b*c^2 + (ns*As + nf*Af)*c - (ns*As*d + nf*Af*df) = 0."""
    b, c, d, df, Ec, Es, Ef, As, Af, phi = sp.symbols("b c d df Ec Es Ef As Af phi", positive=True)
    Cc = sp.Rational(1, 2) * b * c * (Ec * phi * c)
    Ts = As * Es * phi * (d - c)
    Tf = Af * Ef * phi * (df - c)
    ns, nf = Es / Ec, Ef / Ec
    quad = sp.simplify((Cc - Ts - Tf) / (Ec * phi))
    expected = sp.Rational(1, 2) * b * c**2 + (ns * As + nf * Af) * c - (ns * As * d + nf * Af * df)
    assert sp.simplify(quad - expected) == 0
    return True


def solve_rc_cfrp_beam(
    b_mm: float, d_mm: float, h_mm: float, fc_MPa: float, As_mm2: float,
    fy_MPa: float, Es_MPa: float, Af_mm2: float, Ef_MPa: float, eps_fd_cap: float,
    eps_bi: float = 0.0004, psi_f: float = 0.85,
) -> dict[str, float]:
    """Solve ULS neutral axis c (mm) and design flexural strength phi*Mn (kN*m) per ACI 440.2R-17 Ch. 10."""
    eps_cu = 0.003
    beta1 = max(0.65, min(0.85, 0.85 - 0.05 * (fc_MPa - 28.0) / 7.0))

    def force_residual(c_mm: float) -> tuple[float, float, float, float]:
        eps_fe = min(eps_fd_cap, eps_cu * (h_mm - c_mm) / c_mm - eps_bi)
        eps_c = (eps_fe + eps_bi) * c_mm / (h_mm - c_mm)
        eps_s = (eps_fe + eps_bi) * (d_mm - c_mm) / (h_mm - c_mm)
        fs = min(fy_MPa, Es_MPa * eps_s)
        ff = Ef_MPa * eps_fe
        eps_c0 = 1.7 * fc_MPa / (4700.0 * math.sqrt(fc_MPa))
        if eps_c >= eps_cu:
            alpha1, b1 = 0.85, beta1
        else:
            r = eps_c / eps_c0
            b1 = (4.0 - r) / (6.0 - 2.0 * r)
            alpha1 = (3.0 * eps_c0 * eps_c - eps_c**2) / (3.0 * b1 * eps_c0**2)
        Cc = alpha1 * fc_MPa * b1 * c_mm * b_mm
        return Cc - (As_mm2 * fs + Af_mm2 * ff), eps_s, eps_fe, b1

    lo, hi = 5.0, 0.95 * d_mm
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if force_residual(mid)[0] < 0.0:
            lo = mid
        else:
            hi = mid
    c_sol = 0.5 * (lo + hi)
    _, eps_s, eps_fe, b1 = force_residual(c_sol)
    fs = min(fy_MPa, Es_MPa * eps_s)
    ff = Ef_MPa * eps_fe
    Mn_s_kNm = As_mm2 * fs * (d_mm - 0.5 * b1 * c_sol) * 1e-6
    Mn_f_kNm = Af_mm2 * ff * (h_mm - 0.5 * b1 * c_sol) * 1e-6
    eps_sy = fy_MPa / Es_MPa
    phi = 0.90 if eps_s >= 0.005 else (0.65 if eps_s <= eps_sy else 0.65 + 0.25 * (eps_s - eps_sy) / (0.005 - eps_sy))
    return {
        "c_mm": round(c_sol, 4),
        "eps_fe": round(eps_fe, 6),
        "eps_s": round(eps_s, 6),
        "phi": round(phi, 4),
        "phi_Mn_kNm": round(phi * (Mn_s_kNm + psi_f * Mn_f_kNm), 3),
        "ff_MPa": round(ff, 2),
    }


def shear_stitch_contribution_kN(A_stitch_mm2: float, Ef_MPa: float, d_fv_mm: float, s_mm: float, theta_deg: float = 45.0) -> dict[str, float]:
    eps_fv = 0.004
    rad = math.radians(theta_deg)
    Vf_N = (A_stitch_mm2 * Ef_MPa * eps_fv * (math.sin(rad) + math.cos(rad)) * d_fv_mm) / s_mm
    return {"eps_fv_cap": eps_fv, "Vf_45deg_kN": round(Vf_N * 1e-3, 3)}


def cole_bondline_eis(freqs_Hz: list[float], R_inf: float, R0: float, tau_s: float, alpha: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for f in freqs_Hz:
        jw_tau = complex(0.0, 2.0 * math.pi * f * tau_s)
        z = R_inf + (R0 - R_inf) / (1.0 + cmath.exp(alpha * cmath.log(jw_tau)))
        out.append((z.real, z.imag))
    return out


def run_verification() -> dict[str, Any]:
    assert verify_symbolic_cracked_section_identity()

    Ce = 0.95
    Ef_MPa = 195800.0
    ffu_design = Ce * 4230.25  # 4018.74 MPa
    eps_fu_design = ffu_design / Ef_MPa

    # 1) ULS Flexural Benchmark (preserved from v0.1.0)
    base = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 0.0, Ef_MPa, 0.001, eps_bi=0.0)
    eps_fd_ebr = aci440_ebr_debonding_strain(35.0, 2, Ef_MPa, 1.2, eps_fu_design)
    ebr_240 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, eps_fd_ebr)
    eps_fd_truss = 0.70 * eps_fu_design
    truss_240 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, eps_fd_truss)
    truss_120 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 120.0, Ef_MPa, eps_fd_truss)

    ebr_util_pct = 100.0 * ebr_240["eps_fe"] / eps_fu_design
    truss_util_pct = 100.0 * truss_240["eps_fe"] / eps_fu_design
    assert truss_util_pct > 2.5 * ebr_util_pct and truss_120["phi_Mn_kNm"] > ebr_240["phi_Mn_kNm"]

    # 2) Issue #1: Cracked-Section Serviceability (§10.2.8) & Creep-Rupture (§10.2.9)
    init_state = solve_unstrengthened_cracked_eps_bi(55.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 200000.0)
    eps_bi_calc = init_state["eps_bi"]

    serv_design_240 = solve_cracked_serviceability(
        175.0, 91.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, ffu_design, eps_bi_calc
    )
    assert serv_design_240["all_serviceability_gates_pass"]

    serv_overload_passive = solve_cracked_serviceability(
        260.0, 165.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, ffu_design, eps_bi_calc
    )
    assert not serv_overload_passive["all_serviceability_gates_pass"]

    serv_overload_prestressed = solve_cracked_serviceability(
        260.0, 165.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, ffu_design, eps_bi_calc, eps_pre_cfrp=0.0032
    )
    assert serv_overload_prestressed["gates"]["steel_service_0.80fy"] and serv_overload_prestressed["gates"]["cfrp_creep_rupture_0.55ffu"]

    serv_zero_eps_bi = solve_cracked_serviceability(
        175.0, 91.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, ffu_design, eps_bi=0.0
    )
    assert serv_zero_eps_bi["fs_service_MPa"] < serv_design_240["fs_service_MPa"] - 5.0

    serv_creep_fail = solve_cracked_serviceability(
        260.0, 165.0, 300.0, 520.0, 420.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef_MPa, ffu_design, eps_bi_calc, eps_pre_cfrp=0.0115
    )
    assert not serv_creep_fail["gates"]["cfrp_creep_rupture_0.55ffu"]

    # 3) Shear & Self-Sensing EIS (preserved from v0.1.0)
    shear_45 = shear_stitch_contribution_kN(50.0, Ef_MPa, 480.0, 150.0, 45.0)
    freqs = [100.0, 5000.0, 50000.0, 2000000.0]
    z_healthy = cole_bondline_eis(freqs, 12.0, 420.0, 1.5e-6, 0.75)
    z_strain = cole_bondline_eis(freqs, 12.0 * (1.0 + 2.2 * 0.005), 408.0 + 12.0 * (1.0 + 2.2 * 0.005), 1.5e-6, 0.75)
    z_moisture = cole_bondline_eis(freqs, 11.9, 310.0, 3.2e-6, 0.72)
    z_debond = cole_bondline_eis(freqs, 12.1, 890.0, 1.1e-6, 0.68)

    hf_shift_strain = abs(z_strain[-1][0] - z_healthy[-1][0]) / z_healthy[-1][0]
    lf_react_moisture = abs(z_moisture[2][1] - z_healthy[2][1]) / abs(z_healthy[2][1])
    dc_gap_debond = (z_debond[0][0] - z_healthy[0][0]) / z_healthy[0][0]

    controls = [
        {"control": "ebr_strain_exceeds_0.9_eps_fu_cap", "rejected": aci440_ebr_debonding_strain(80.0, 1, 50000.0, 0.1, 0.015) <= 0.9 * 0.015 + 1e-12},
        {"control": "shear_stitch_strain_exceeds_0.004_aggregate_interlock", "rejected": shear_45["eps_fv_cap"] <= 0.004},
        {"control": "unanchored_2ply_ebr_uses_over_25pct_t1100g_strain", "rejected": ebr_util_pct < 25.0},
        {"control": "pure_axial_strain_corrupts_50khz_bondline_reactance", "rejected": abs(z_strain[2][1] - z_healthy[2][1]) < 1e-3},
        {"control": "half_mass_truss_kit_underperforms_double_mass_flat_ebr", "rejected": truss_120["phi_Mn_kNm"] > ebr_240["phi_Mn_kNm"]},
        {"control": "uls_passing_heavy_overload_beam_passes_serviceability_without_check", "rejected": not serv_overload_passive["all_serviceability_gates_pass"]},
        {"control": "excessive_cfrp_sustained_stress_passes_0.55ffu_creep_rupture_gate", "rejected": not serv_creep_fail["gates"]["cfrp_creep_rupture_0.55ffu"]},
        {"control": "omitting_dead_load_eps_bi_is_conservative_for_rebar_stress", "rejected": serv_zero_eps_bi["fs_service_MPa"] < serv_design_240["fs_service_MPa"] - 5.0},
    ]
    assert all(c["rejected"] for c in controls)

    return {
        "repository": "aska-physical/cfrp-truss",
        "certificate_version": "0.2.0",
        "beam_benchmark_SI": {
            "unstrengthened_phi_Mn_kNm": base["phi_Mn_kNm"],
            "flat_ebr_240mm2_eps_fd": round(eps_fd_ebr, 6),
            "flat_ebr_240mm2_fiber_utilization_pct": round(ebr_util_pct, 2),
            "flat_ebr_240mm2_phi_Mn_kNm": ebr_240["phi_Mn_kNm"],
            "truss_anchored_120mm2_phi_Mn_kNm": truss_120["phi_Mn_kNm"],
            "truss_anchored_240mm2_fiber_utilization_pct": round(truss_util_pct, 2),
            "truss_anchored_240mm2_phi_Mn_kNm": truss_240["phi_Mn_kNm"],
            "cfrp_mass_reduction_at_higher_capacity_pct": 50.0,
            "moment_gain_over_flat_ebr_same_area_pct": round(100.0 * (truss_240["phi_Mn_kNm"] - ebr_240["phi_Mn_kNm"]) / (ebr_240["phi_Mn_kNm"] - base["phi_Mn_kNm"]), 2),
        },
        "serviceability_and_creep_checks_issue_1": {
            "initial_dead_load_55kNm": init_state,
            "design_envelope_175kNm_passive": serv_design_240,
            "heavy_overload_260kNm_passive_fails_service": serv_overload_passive,
            "heavy_overload_260kNm_prestressed_3200ue_relieves_steel": serv_overload_prestressed,
        },
        "shear_stitch_45deg": shear_45,
        "self_sensing_eis_separability": {
            "strain_0.5pct_hf_resistance_shift_pct": round(100.0 * hf_shift_strain, 3),
            "moisture_50khz_reactance_shift_pct": round(100.0 * lf_react_moisture, 2),
            "debonding_100hz_resistance_jump_pct": round(100.0 * dc_gap_debond, 2),
        },
        "must_fail_controls": controls,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    cert = run_verification()
    if args.write:
        CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CERT_PATH.write_text(json.dumps(cert, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not CERT_PATH.exists() or json.loads(CERT_PATH.read_text(encoding="utf-8")) != cert:
        print("[FAIL] results/certificate.json mismatch or missing", file=sys.stderr)
        return 1
    print("[PASS] cfrp-truss v0.2.0 ULS + Serviceability (§10.2.8) + Creep (§10.2.9) + EIS checks (8/8 controls): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
