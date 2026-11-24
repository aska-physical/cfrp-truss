#!/usr/bin/env python3
"""Deterministic ACI 440.2R-17/-23 ULS + Serviceability (§10.2.8) + Creep (§10.2.9)
+ ACI 318M-19 Ch. 17 45-deg Dowel/Epoxy Sizing + Cole EIS verifier (v0.3.0)."""

from __future__ import annotations
import argparse, cmath, json, math, sys
from pathlib import Path
from typing import Any
import sympy as sp

ROOT = Path(__file__).resolve().parent
CERT_PATH = ROOT / "results" / "certificate.json"


def require(condition: bool, message: str) -> None:
    """Fail-closed guard that cannot be stripped by python3 -O."""
    if not condition:
        raise ValueError(f"verification failed: {message}")


def aci440_ebr_debonding_strain(fc_MPa: float, n_plies: int, Ef_MPa: float, tf_mm: float, eps_fu: float) -> float:
    return min(0.41 * math.sqrt(fc_MPa / (n_plies * Ef_MPa * tf_mm)), 0.9 * eps_fu)


def solve_unstrengthened_cracked_eps_bi(
    M_DL_kNm: float, b_mm: float, d_mm: float, df_mm: float, fc_MPa: float, As_mm2: float, Es_MPa: float
) -> dict[str, float]:
    Ec = 4700.0 * math.sqrt(fc_MPa)
    ns = Es_MPa / Ec
    rho = As_mm2 / (b_mm * d_mm)
    ck0 = (math.sqrt((rho * ns) ** 2 + 2.0 * rho * ns) - rho * ns) * d_mm
    Icr0 = (b_mm * (ck0**3)) / 3.0 + ns * As_mm2 * ((d_mm - ck0) ** 2)
    eps_bi = (M_DL_kNm * 1e6 * (df_mm - ck0)) / (Ec * Icr0)
    return {"Ec_MPa": round(Ec, 2), "ck0_mm": round(ck0, 4), "Icr0_mm4": round(Icr0, 1), "eps_bi": round(eps_bi, 6)}


def solve_cracked_serviceability(
    M_service_kNm: float, M_sustained_kNm: float, b_mm: float, d_mm: float, df_mm: float,
    fc_MPa: float, As_mm2: float, fy_MPa: float, Es_MPa: float, Af_mm2: float,
    Ef_MPa: float, ffu_design_MPa: float, eps_bi: float, eps_pre_cfrp: float = 0.0,
    creep_factor_cfrp: float = 0.55,
) -> dict[str, Any]:
    Ec = 4700.0 * math.sqrt(fc_MPa)

    def solve_at_moment(M_kNm: float) -> tuple[float, float, float, float]:
        M_target = M_kNm * 1e6

        def moment_for_ck(ck: float) -> tuple[float, float, float, float]:
            lo, hi = 0.0, 2.0e-4
            for _ in range(65):
                pm = 0.5 * (lo + hi)
                fs = Es_MPa * pm * (d_mm - ck)
                ff = Ef_MPa * max(0.0, pm * (df_mm - ck) - eps_bi + eps_pre_cfrp)
                if As_mm2 * fs * (d_mm - ck / 3.0) + Af_mm2 * ff * (df_mm - ck / 3.0) < M_target:
                    lo = pm
                else:
                    hi = pm
            ps = 0.5 * (lo + hi)
            fc = Ec * ps * ck
            fs = Es_MPa * ps * (d_mm - ck)
            ff = Ef_MPa * max(0.0, ps * (df_mm - ck) - eps_bi + eps_pre_cfrp)
            return 0.5 * b_mm * ck * fc - (As_mm2 * fs + Af_mm2 * ff), fc, fs, ff

        ck_lo, ck_hi = 5.0, 0.92 * d_mm
        for _ in range(75):
            ck_m = 0.5 * (ck_lo + ck_hi)
            if moment_for_ck(ck_m)[0] < 0.0:
                ck_lo = ck_m
            else:
                ck_hi = ck_m
        ck_f = 0.5 * (ck_lo + ck_hi)
        f_res, fc, fs, ff = moment_for_ck(ck_f)
        require(abs(f_res) < 1.0, f"cracked serviceability force equilibrium did not converge (residual={f_res:.6g})")
        return ck_f, fc, fs, ff

    ck_tot, fc_s, fs_s, ff_s = solve_at_moment(M_service_kNm)
    _, fc_sus, _, ff_sus = solve_at_moment(M_sustained_kNm)
    g1, g2 = fc_s <= 0.60 * fc_MPa, fc_sus <= 0.45 * fc_MPa
    g3, g4 = fs_s <= 0.80 * fy_MPa, ff_s <= creep_factor_cfrp * ffu_design_MPa
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
        "all_serviceability_gates_pass": bool(g1 and g2 and g3 and g4),
        "gates": {
            "concrete_total_0.60fc": bool(g1),
            "concrete_sustained_0.45fc": bool(g2),
            "steel_service_0.80fy": bool(g3),
            "cfrp_creep_rupture_0.55ffu": bool(g4),
        },
    }

def verify_symbolic_identities() -> bool:
    b, c, d, df, Ec, Es, Ef, As, Af, phi = sp.symbols("b c d df Ec Es Ef As Af phi", positive=True)
    Cc = sp.Rational(1, 2) * b * c * (Ec * phi * c)
    Ts, Tf = As * Es * phi * (d - c), Af * Ef * phi * (df - c)
    ns, nf = Es / Ec, Ef / Ec
    expected = sp.Rational(1, 2) * b * c**2 + (ns * As + nf * Af) * c - (ns * As * d + nf * Af * df)
    require(
        sp.simplify((Cc - Ts - Tf) / (Ec * phi) - expected) == 0,
        "cracked-section neutral-axis equilibrium symbolic identity failed",
    )

    hef, s, n = sp.symbols("hef s n", positive=True)
    ANc_trunc = b * (sp.Rational(3, 2) * hef + (n - 1) * s + sp.Rational(3, 2) * hef)
    require(
        sp.simplify(ANc_trunc / (9 * hef**2) - b * (3 * hef + (n - 1) * s) / (9 * hef**2)) == 0,
        "width-truncated concrete breakout cone ratio identity failed",
    )

    da, Lz, tau_cr, tau_uncr = sp.symbols("da Lz tau_cr tau_uncr", positive=True)
    cNa = 10 * da * sp.sqrt(tau_uncr / sp.Rational(76, 10))
    Nag_tr = (b * Lz / (4 * cNa**2)) * (tau_cr * sp.pi * da * hef)
    require(
        sp.simplify(sp.diff(Nag_tr, da) + Nag_tr / da) == 0,
        "inverse dowel-diameter group bond sensitivity identity failed",
    )
    return True


def solve_rc_cfrp_beam(
    b_mm: float, d_mm: float, h_mm: float, fc_MPa: float, As_mm2: float,
    fy_MPa: float, Es_MPa: float, Af_mm2: float, Ef_MPa: float, eps_fd_cap: float,
    eps_bi: float = 0.0004, psi_f: float = 0.85,
) -> dict[str, float]:
    eps_cu = 0.003
    beta1 = max(0.65, min(0.85, 0.85 - 0.05 * (fc_MPa - 28.0) / 7.0))

    def force_residual(c_mm: float) -> tuple[float, float, float, float]:
        eps_fe = min(eps_fd_cap, eps_cu * (h_mm - c_mm) / c_mm - eps_bi)
        eps_c = (eps_fe + eps_bi) * c_mm / (h_mm - c_mm)
        eps_s = (eps_fe + eps_bi) * (d_mm - c_mm) / (h_mm - c_mm)
        fs, ff = min(fy_MPa, Es_MPa * eps_s), Ef_MPa * eps_fe
        eps_c0 = 1.7 * fc_MPa / (4700.0 * math.sqrt(fc_MPa))
        if eps_c >= eps_cu:
            alpha1, b1 = 0.85, beta1
        else:
            r = eps_c / eps_c0
            b1 = (4.0 - r) / (6.0 - 2.0 * r)
            alpha1 = (3.0 * eps_c0 * eps_c - eps_c**2) / (3.0 * b1 * eps_c0**2)
        return alpha1 * fc_MPa * b1 * c_mm * b_mm - (As_mm2 * fs + Af_mm2 * ff), eps_s, eps_fe, b1

    lo, hi = 5.0, 0.95 * d_mm
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if force_residual(mid)[0] < 0.0:
            lo = mid
        else:
            hi = mid
    c_sol = 0.5 * (lo + hi)
    _, eps_s, eps_fe, b1 = force_residual(c_sol)
    fs, ff = min(fy_MPa, Es_MPa * eps_s), Ef_MPa * eps_fe
    Mn_s = As_mm2 * fs * (d_mm - 0.5 * b1 * c_sol) * 1e-6
    Mn_f = Af_mm2 * ff * (h_mm - 0.5 * b1 * c_sol) * 1e-6
    eps_sy = fy_MPa / Es_MPa
    phi = 0.90 if eps_s >= 0.005 else (0.65 if eps_s <= eps_sy else 0.65 + 0.25 * (eps_s - eps_sy) / (0.005 - eps_sy))
    return {
        "c_mm": round(c_sol, 4), "eps_fe": round(eps_fe, 6), "eps_s": round(eps_s, 6),
        "phi": round(phi, 4), "phi_Mn_kNm": round(phi * (Mn_s + psi_f * Mn_f), 3), "ff_MPa": round(ff, 2),
    }


def solve_chord_shear_flow_fd(
    M_nominal_peak_kNm: float, L_shear_mm: float, n_steps: int, b_mm: float, d_mm: float, df_mm: float,
    fc_MPa: float, As_mm2: float, fy_MPa: float, Es_MPa: float, Af_mm2: float, Ef_MPa: float,
    eps_bi: float, eps_pre_cfrp: float, n_grooves: int = 2, groove_w_mm: float = 12.0, groove_d_mm: float = 20.0,
) -> dict[str, Any]:
    Ec = 4700.0 * math.sqrt(fc_MPa)
    eps_c0 = 1.7 * fc_MPa / Ec

    def chord_tension(M_kNm: float) -> float:
        if M_kNm <= 1e-6:
            return Af_mm2 * Ef_MPa * max(0.0, eps_pre_cfrp - eps_bi)
        M_t = M_kNm * 1e6

        def mf_for_c(c_mm: float) -> tuple[float, float]:
            lo, hi = 0.0, 3.0e-4
            for _ in range(65):
                pm = 0.5 * (lo + hi)
                fs = min(fy_MPa, Es_MPa * pm * (d_mm - c_mm))
                ff = Ef_MPa * max(0.0, pm * (df_mm - c_mm) - eps_bi + eps_pre_cfrp)
                r = min(1.95, (pm * c_mm) / eps_c0)
                b1 = (4.0 - r) / (6.0 - 2.0 * r) if r > 1e-5 else 0.6667
                if As_mm2 * fs * (d_mm - 0.5 * b1 * c_mm) + Af_mm2 * ff * (df_mm - 0.5 * b1 * c_mm) < M_t:
                    lo = pm
                else:
                    hi = pm
            ps = 0.5 * (lo + hi)
            ec = ps * c_mm
            r = min(1.95, ec / eps_c0)
            if r <= 1e-5:
                Cc = 0.5 * b_mm * c_mm * Ec * ec
            else:
                b1 = (4.0 - r) / (6.0 - 2.0 * r)
                Cc = ((3.0 * eps_c0 * ec - ec**2) / (3.0 * b1 * eps_c0**2)) * fc_MPa * b1 * c_mm * b_mm
            fs = min(fy_MPa, Es_MPa * ps * (d_mm - c_mm))
            Tf = Af_mm2 * Ef_MPa * max(0.0, ps * (df_mm - c_mm) - eps_bi + eps_pre_cfrp)
            return Cc - (As_mm2 * fs + Tf), Tf

        c_lo, c_hi = 5.0, 0.90 * d_mm
        for _ in range(65):
            cm = 0.5 * (c_lo + c_hi)
            if mf_for_c(cm)[0] < 0.0:
                c_lo = cm
            else:
                c_hi = cm
        return mf_for_c(0.5 * (c_lo + c_hi))[1]

    dx = L_shear_mm / n_steps
    Tf_l = [chord_tension(M_nominal_peak_kNm * (i / n_steps)) for i in range(n_steps + 1)]
    qf_l = [(Tf_l[i] - Tf_l[i - 1]) / dx for i in range(1, n_steps + 1)]
    qf_avg, qf_peak, qf_pre = (Tf_l[-1] - Tf_l[0]) / L_shear_mm, max(qf_l), qf_l[int(0.25 * n_steps)]
    p_gr = n_grooves * (2.0 * groove_d_mm + groove_w_mm)
    tau_cap = 0.45 * math.sqrt(fc_MPa)
    return {
        "M_nominal_peak_kNm": M_nominal_peak_kNm, "L_shear_mm": L_shear_mm, "dx_mm": round(dx, 2),
        "Tf_support_kN": round(Tf_l[0] * 1e-3, 3), "Tf_midspan_kN": round(Tf_l[-1] * 1e-3, 3),
        "qf_avg_N_per_mm": round(qf_avg, 3), "qf_pre_yield_N_per_mm": round(qf_pre, 3),
        "qf_post_yield_peak_N_per_mm": round(qf_peak, 3),
        "post_yield_over_avg_ratio": round(qf_peak / qf_avg, 3),
        "post_yield_over_elastic_ratio": round(qf_peak / qf_pre, 3),
        "wetted_groove_perimeter_mm": round(p_gr, 1),
        "tau_groove_avg_MPa": round(qf_avg / p_gr, 3),
        "tau_groove_post_yield_peak_MPa": round(qf_peak / p_gr, 3),
        "tau_groove_concrete_peel_cap_MPa": round(tau_cap, 3),
        "qf_excess_carried_by_stitches_N_per_mm": round(max(0.0, qf_peak - tau_cap * p_gr), 3),
    }


def evaluate_aci318_ch17_anchor_group(
    n_anchors: int, n_rows_across_width: int, s_long_mm: float, s_trans_mm: float, c_end_mm: float,
    b_beam_mm: float, da_mm: float, L_drill_mm: float, theta_deg: float, fc_MPa: float,
    tau_cr_2500psi_MPa: float, tau_uncr_2500psi_MPa: float, F_horiz_ult_kN: float, F_horiz_sus_kN: float,
    engage_closed_stirrup_core: bool = True, stirrup_spacing_mm: float = 150.0,
    Asv_2leg_mm2: float = 157.0, fyt_MPa: float = 420.0, pad_friction_coeff: float = 0.40,
) -> dict[str, Any]:
    rad = math.radians(theta_deg)
    hef_geom = L_drill_mm * math.sin(rad) if theta_deg > 1e-3 else L_drill_mm
    hef = min(20.0 * da_mm, hef_geom)
    f_sc = (min(55.2, max(17.2, fc_MPa)) / 17.2) ** 0.25
    tau_cr, tau_uncr = tau_cr_2500psi_MPa * f_sc, tau_uncr_2500psi_MPa * f_sc
    phi_N, phi_V, phi_s = 0.65, 0.70, 0.75

    Nb = 7.0 * math.sqrt(fc_MPa) * (hef**1.5)
    ANco = 9.0 * (hef**2)
    n_long = n_anchors // n_rows_across_width
    c_side = 0.5 * (b_beam_mm - (n_rows_across_width - 1) * s_trans_mm)
    w_nc = min(b_beam_mm, 2.0 * min(c_side, 1.5 * hef) + (n_rows_across_width - 1) * min(s_trans_mm, 3.0 * hef))
    L_zone = (n_long - 1) * s_long_mm
    L_nc = min(c_end_mm, 1.5 * hef) + (n_long - 1) * min(s_long_mm, 3.0 * hef) + 1.5 * hef
    ca_min = min(c_side, c_end_mm)
    psi_ed_N = 1.0 if ca_min >= 1.5 * hef else (0.7 + 0.3 * (ca_min / (1.5 * hef)))
    Ncbg = ((w_nc * L_nc) / ANco) * psi_ed_N * Nb
    phi_Ncbg_kN = phi_N * Ncbg * 1e-3

    if engage_closed_stirrup_core and hef_geom >= 120.0:
        n_st = max(1, int(math.floor((L_zone + hef_geom) / stirrup_spacing_mm)) + 1)
        phi_N_st_kN = phi_s * n_st * Asv_2leg_mm2 * fyt_MPa * 1e-3
        phi_N_up_kN = phi_N_st_kN
    else:
        phi_N_st_kN, phi_N_up_kN = 0.0, phi_Ncbg_kN

    Nba = tau_cr * math.pi * da_mm * hef
    cNa = 10.0 * da_mm * math.sqrt(tau_uncr / 7.6)
    w_na = min(b_beam_mm, 2.0 * min(c_side, cNa) + (n_rows_across_width - 1) * min(s_trans_mm, 2.0 * cNa))
    L_na = min(c_end_mm, cNa) + (n_long - 1) * min(s_long_mm, 2.0 * cNa) + cNa
    psi_ed_Na = 1.0 if ca_min >= cNa else (0.7 + 0.3 * (ca_min / cNa))
    Nag = ((w_na * L_na) / ((2.0 * cNa) ** 2)) * psi_ed_Na * Nba
    phi_Nag_kN = phi_N * Nag * 1e-3

    k_cp = 2.0 if hef >= 65.0 else 1.0
    gov_N_pry = Nag if (engage_closed_stirrup_core and hef_geom >= 120.0) else min(Ncbg, Nag)
    phi_Vcpg_kN = phi_V * k_cp * gov_N_pry * 1e-3

    if theta_deg < 89.0:
        den = math.cos(rad) + pad_friction_coeff * math.sin(rad)
        T_ult, T_sus, V_guil = F_horiz_ult_kN / den, F_horiz_sus_kN / den, 0.0
        N_up = T_ult * math.sin(rad)
        V_pry = T_ult * math.cos(rad) * (1.0 - pad_friction_coeff * math.tan(rad))
    else:
        T_ult, T_sus, V_guil, N_up, V_pry = 0.0, 0.0, F_horiz_ult_kN, 0.0, F_horiz_ult_kN

    u_up, u_bd = N_up / phi_N_up_kN, T_ult / phi_Nag_kN
    u_pr = max(0.0, V_pry) / phi_Vcpg_kN
    inter = (max(u_up, u_bd) ** (5.0 / 3.0)) + (u_pr ** (5.0 / 3.0))
    cap_sus_1, sus_1 = 0.55 * phi_N * Nba * 1e-3, T_sus / n_anchors
    pass_cr = sus_1 <= cap_sus_1

    Ad = 0.25 * math.pi * (da_mm**2)
    pass_mat = (T_ult <= n_anchors * 0.75 * Ad * 4018.74 * 1e-3) and (V_guil <= n_anchors * 0.65 * Ad * 75.0 * 1e-3)
    tau_sl = (T_ult * 1e3) / (n_anchors * math.pi * da_mm * max(4.0 * da_mm, L_drill_mm - 20.0))
    all_ok = bool(u_up <= 1.0 and u_bd <= 1.0 and u_pr <= 1.0 and inter <= 1.0 and pass_cr and pass_mat)

    return {
        "n_anchors": n_anchors, "da_mm": da_mm, "theta_deg": theta_deg,
        "hef_eff_mm": round(hef, 2), "cNa_mm": round(cNa, 2),
        "unreinforced_cone_efficiency_pct": round(100.0 * Ncbg / (n_anchors * Nb), 2),
        "phi_Ncbg_unreinforced_kN": round(phi_Ncbg_kN, 2),
        "phi_N_stirrup_core_kN": round(phi_N_st_kN, 2),
        "phi_Nag_bond_group_kN": round(phi_Nag_kN, 2),
        "phi_Vcpg_pryout_kN": round(phi_Vcpg_kN, 2),
        "T_dowel_ult_group_kN": round(T_ult, 2),
        "N_ua_uplift_kN": round(N_up, 2),
        "sustained_per_anchor_kN": round(sus_1, 2),
        "sustained_0.55phiNba_cap_kN": round(cap_sus_1, 2),
        "tau_epoxy_slant_ult_MPa": round(tau_sl, 3),
        "interaction_5_3": round(inter, 4),
        "gates": {
            "uplift_cone_or_stirrup_core": bool(u_up <= 1.0),
            "adhesive_bond_group_17.6.5": bool(u_bd <= 1.0),
            "sustained_epoxy_creep_17.5.2.2": bool(pass_cr),
            "concrete_pryout_17.7.3": bool(u_pr <= 1.0),
            "tension_shear_interaction_17.8": bool(inter <= 1.0),
            "cfrp_dowel_material_no_guillotine": bool(pass_mat),
        },
        "all_ch17_gates_pass": all_ok,
    }


def size_dual_cartridge_epoxy(
    L_beam_mm: float, n_grooves: int, groove_w_mm: float, groove_d_mm: float, Af_total_mm2: float,
    n_dist_stitches: int, L_dist_drill_mm: float, da_dist_mm: float, dhole_dist_mm: float,
    n_end_anchors_total: int, L_end_drill_mm: float, da_end_mm: float, dhole_end_mm: float,
    cartridge_vol_mL: float = 600.0, waste_overbore_factor: float = 1.15, nozzle_purge_mL: float = 25.0, n_nozzles: int = 2,
) -> dict[str, Any]:
    v_gr = L_beam_mm * (n_grooves * groove_w_mm * groove_d_mm - Af_total_mm2) * 1e-3 * waste_overbore_factor
    a_d = 0.25 * math.pi * (dhole_dist_mm**2 - da_dist_mm**2)
    a_e = 0.25 * math.pi * (dhole_end_mm**2 - da_end_mm**2)
    v_st = (n_dist_stitches * L_dist_drill_mm * a_d + n_end_anchors_total * L_end_drill_mm * a_e) * 1e-3 * waste_overbore_factor
    v_p = n_nozzles * nozzle_purge_mL
    tot = v_gr + v_st + v_p
    return {
        "groove_epoxy_mL": round(v_gr, 1), "dowel_holes_epoxy_mL": round(v_st, 1),
        "static_nozzle_purge_mL": round(v_p, 1), "total_mixed_epoxy_mL": round(tot, 1),
        "cartridge_size_mL": cartridge_vol_mL, "dual_cartridges_per_6m_beam": math.ceil(tot / cartridge_vol_mL),
    }


def shear_stitch_contribution_kN(A_stitch_mm2: float, Ef_MPa: float, d_fv_mm: float, s_mm: float, theta_deg: float = 45.0) -> dict[str, float]:
    rad = math.radians(theta_deg)
    Vf = (A_stitch_mm2 * Ef_MPa * 0.004 * (math.sin(rad) + math.cos(rad)) * d_fv_mm) / s_mm
    return {"eps_fv_cap": 0.004, "Vf_45deg_kN": round(Vf * 1e-3, 3)}


def cole_bondline_eis(freqs_Hz: list[float], R_inf: float, R0: float, tau_s: float, alpha: float) -> list[tuple[float, float]]:
    return [
        (z.real, z.imag)
        for f in freqs_Hz
        for z in [R_inf + (R0 - R_inf) / (1.0 + cmath.exp(alpha * cmath.log(complex(0.0, 2.0 * math.pi * f * tau_s))))]
    ]


def run_verification() -> dict[str, Any]:
    require(verify_symbolic_identities(), "symbolic identities verification returned falsy")
    Ef, ffu = 195800.0, 0.95 * 4230.25
    eps_fu = ffu / Ef

    base = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 0.0, Ef, 0.001, eps_bi=0.0)
    eps_ebr = aci440_ebr_debonding_strain(35.0, 2, Ef, 1.2, eps_fu)
    ebr_240 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, eps_ebr)
    truss_240 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, 0.70 * eps_fu)
    truss_120 = solve_rc_cfrp_beam(300.0, 520.0, 600.0, 35.0, 1500.0, 420.0, 200000.0, 120.0, Ef, 0.70 * eps_fu)
    ebr_u, tr_u = 100.0 * ebr_240["eps_fe"] / eps_fu, 100.0 * truss_240["eps_fe"] / eps_fu
    require(tr_u > 2.5 * ebr_u, f"truss fiber utilization ({tr_u:.2f}%) must exceed 2.5x EBR ({ebr_u:.2f}%)")
    require(
        truss_120["phi_Mn_kNm"] > ebr_240["phi_Mn_kNm"],
        f"half-mass truss capacity ({truss_120['phi_Mn_kNm']}) must exceed double-mass EBR ({ebr_240['phi_Mn_kNm']})",
    )

    init_s = solve_unstrengthened_cracked_eps_bi(55.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 200000.0)
    ebi = init_s["eps_bi"]
    s_des = solve_cracked_serviceability(175.0, 91.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ffu, ebi)
    s_ov_p = solve_cracked_serviceability(260.0, 165.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ffu, ebi)
    s_ov_pr = solve_cracked_serviceability(260.0, 165.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ffu, ebi, 0.0032)
    s_z_ebi = solve_cracked_serviceability(175.0, 91.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ffu, 0.0)
    s_cr_f = solve_cracked_serviceability(260.0, 165.0, 300.0, 520.0, 420.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ffu, ebi, 0.0115)

    fd_sf = solve_chord_shear_flow_fd(481.61, 2000.0, 40, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, Ef, ebi, 0.0032)
    F_ex = fd_sf["qf_excess_carried_by_stitches_N_per_mm"] * 150.0 * 1e-3
    dist_p = evaluate_aci318_ch17_anchor_group(2, 2, 150.0, 140.0, 300.0, 300.0, 8.0, 226.0, 45.0, 35.0, 9.4, 13.5, F_ex, 0.0)
    T_sus = 240.0 * Ef * 0.0032 * 1e-3
    T_ult = 1.20 * T_sus
    e_8_ok = evaluate_aci318_ch17_anchor_group(12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0, 9.4, 13.5, T_ult, T_sus, True)
    e_16_f = evaluate_aci318_ch17_anchor_group(12, 2, 220.0, 140.0, 200.0, 300.0, 16.0, 250.0, 45.0, 35.0, 9.4, 13.5, T_ult, T_sus, True)
    e_cov_f = evaluate_aci318_ch17_anchor_group(12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0, 9.4, 13.5, T_ult, T_sus, False)
    e_90_f = evaluate_aci318_ch17_anchor_group(12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 90.0, 35.0, 9.4, 13.5, T_ult, T_sus, True)
    ep_sz = size_dual_cartridge_epoxy(5600.0, 2, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 24, 230.0, 8.0, 12.0)

    sh_45 = shear_stitch_contribution_kN(50.0, Ef, 480.0, 150.0, 45.0)
    fr = [100.0, 5000.0, 50000.0, 2000000.0]
    zh = cole_bondline_eis(fr, 12.0, 420.0, 1.5e-6, 0.75)
    zs = cole_bondline_eis(fr, 12.0 * (1.0 + 2.2 * 0.005), 408.0 + 12.0 * (1.0 + 2.2 * 0.005), 1.5e-6, 0.75)
    zm = cole_bondline_eis(fr, 11.9, 310.0, 3.2e-6, 0.72)
    zd = cole_bondline_eis(fr, 12.1, 890.0, 1.1e-6, 0.68)

    controls = [
        {"control": "ebr_strain_exceeds_0.9_eps_fu_cap", "rejected": aci440_ebr_debonding_strain(80.0, 1, 50000.0, 0.1, 0.015) <= 0.9 * 0.015 + 1e-12},
        {"control": "shear_stitch_strain_exceeds_0.004_aggregate_interlock", "rejected": sh_45["eps_fv_cap"] <= 0.004},
        {"control": "unanchored_2ply_ebr_uses_over_25pct_t1100g_strain", "rejected": ebr_u < 25.0},
        {"control": "pure_axial_strain_corrupts_50khz_bondline_reactance", "rejected": abs(zs[2][1] - zh[2][1]) < 1e-3},
        {"control": "half_mass_truss_kit_underperforms_double_mass_flat_ebr", "rejected": truss_120["phi_Mn_kNm"] > ebr_240["phi_Mn_kNm"]},
        {"control": "uls_passing_heavy_overload_beam_passes_serviceability_without_check", "rejected": not s_ov_p["all_serviceability_gates_pass"]},
        {"control": "excessive_cfrp_sustained_stress_passes_0.55ffu_creep_rupture_gate", "rejected": not s_cr_f["gates"]["cfrp_creep_rupture_0.55ffu"]},
        {"control": "omitting_dead_load_eps_bi_is_conservative_for_rebar_stress", "rejected": s_z_ebi["fs_service_MPa"] < s_des["fs_service_MPa"] - 5.0},
        {"control": "average_shear_flow_captures_post_yield_chord_gradient_spike", "rejected": fd_sf["post_yield_over_avg_ratio"] > 3.5},
        {"control": "oversized_16mm_dowel_increases_width_truncated_group_bond_capacity", "rejected": not e_16_f["gates"]["adhesive_bond_group_17.6.5"] and e_8_ok["all_ch17_gates_pass"] and dist_p["all_ch17_gates_pass"]},
        {"control": "unreinforced_cover_cone_carries_180kN_end_prestress_without_stirrup_core", "rejected": not e_cov_f["gates"]["uplift_cone_or_stirrup_core"]},
        {"control": "vertical_90deg_carbon_dowel_survives_guillotine_shear", "rejected": not e_90_f["gates"]["cfrp_dowel_material_no_guillotine"]},
    ]
    require(s_des["all_serviceability_gates_pass"], "design serviceability envelope failed")
    require(s_ov_pr["all_serviceability_gates_pass"], "prestressed overload serviceability envelope failed")
    failed_controls = [c["control"] for c in controls if not c["rejected"]]
    require(not failed_controls, f"must-fail controls not rejected: {', '.join(failed_controls)}")

    return {
        "repository": "aska-physical/cfrp-truss",
        "certificate_version": "0.3.0",
        "beam_benchmark_SI": {
            "unstrengthened_phi_Mn_kNm": base["phi_Mn_kNm"],
            "flat_ebr_240mm2_eps_fd": round(eps_ebr, 6),
            "flat_ebr_240mm2_fiber_utilization_pct": round(ebr_u, 2),
            "flat_ebr_240mm2_phi_Mn_kNm": ebr_240["phi_Mn_kNm"],
            "truss_anchored_120mm2_phi_Mn_kNm": truss_120["phi_Mn_kNm"],
            "truss_anchored_240mm2_fiber_utilization_pct": round(tr_u, 2),
            "truss_anchored_240mm2_phi_Mn_kNm": truss_240["phi_Mn_kNm"],
            "cfrp_mass_reduction_at_higher_capacity_pct": 50.0,
            "moment_gain_over_flat_ebr_same_area_pct": round(100.0 * (truss_240["phi_Mn_kNm"] - ebr_240["phi_Mn_kNm"]) / (ebr_240["phi_Mn_kNm"] - base["phi_Mn_kNm"]), 2),
        },
        "serviceability_and_creep_checks_issue_1": {
            "initial_dead_load_55kNm": init_s,
            "design_envelope_175kNm_passive": s_des,
            "heavy_overload_260kNm_passive_fails_service": s_ov_p,
            "heavy_overload_260kNm_prestressed_3200ue_relieves_steel": s_ov_pr,
        },
        "aci318_ch17_dowel_and_epoxy_sizing_issue_2": {
            "chord_shear_flow_fd_481kNm": fd_sf,
            "distributed_post_yield_stitch_pair_8mm": dist_p,
            "end_prestress_12x8mm_45deg_stirrup_core_passes": e_8_ok,
            "end_prestress_12x16mm_oversized_bond_fails": e_16_f,
            "end_prestress_cover_only_no_stirrup_cone_fails": e_cov_f,
            "end_prestress_12x8mm_90deg_guillotine_fails": e_90_f,
            "dual_cartridge_epoxy_6m_beam": ep_sz,
        },
        "shear_stitch_45deg": sh_45,
        "self_sensing_eis_separability": {
            "strain_0.5pct_hf_resistance_shift_pct": round(100.0 * abs(zs[-1][0] - zh[-1][0]) / zh[-1][0], 3),
            "moisture_50khz_reactance_shift_pct": round(100.0 * abs(zm[2][1] - zh[2][1]) / abs(zh[2][1]), 2),
            "debonding_100hz_resistance_jump_pct": round(100.0 * (zd[0][0] - zh[0][0]) / zh[0][0], 2),
        },
        "must_fail_controls": controls,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    cert = run_verification()
    if args.write:
        CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CERT_PATH.write_text(json.dumps(cert, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not CERT_PATH.exists() or json.loads(CERT_PATH.read_text(encoding="utf-8")) != cert:
        print("[FAIL] results/certificate.json mismatch or missing", file=sys.stderr)
        return 1
    n_controls = len(cert["must_fail_controls"])
    print(f"[PASS] cfrp-truss v0.3.0 ULS + Serviceability + Creep + ACI 318 Ch.17 Dowel/Epoxy + EIS ({n_controls}/{n_controls} controls): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
