#!/usr/bin/env python3
"""Zero-bare-assert R5 physical hardening regression tests for aska-physical/cfrp-truss (v0.3.1).

Verifies:
  1. Bar-axis crack-plane transverse shear V_bar = F_horiz * sin(theta) is enforced continuously at
     every embedment angle theta in (0, 90] (closing the v0.3.0 V_guil = 0 bypass for theta < 89 deg).
  2. Single-counted pad-friction horizontal equilibrium V_pry + mu * N_up == F_horiz (closing the
     v0.3.0 double-counted friction bug that understated V_pry by 40% at 45 deg, mu = 0.40).
  3. Honest negative findings on bare 45-deg unidirectional CFRP dowels (75 MPa ILSS) and on 14 x 8 mm
     ISO 898-1 Class 8.8 steel under ACI 318-19 §17.5.3 brittle factors (phi_N=0.65, phi_V=0.60) and
     R17.8 5/3 / §17.8.3 trilinear interaction, PLUS verified passing 16 x 8 mm end group & 2 x 8 mm
     Class 8.8 steel stitches with programmatically tied dual-cartridge epoxy sizing (32 end dowels).
  4. Fail-closed validation of theta_deg, pad_friction_coeff, horizontal loads, phi_N_bar/phi_V_bar,
     and material limits, plus zero bare assert statements across verify.py and this suite.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import verify


class TestCfrpTrussPhysicalHardening(unittest.TestCase):
    def test_zero_bare_asserts_and_zero_builtin_maxmin(self) -> None:
        v_tree = ast.parse((ROOT / "verify.py").read_text(encoding="utf-8"))
        self.assertEqual([n.lineno for n in ast.walk(v_tree) if isinstance(n, ast.Assert)], [])
        maxmin = [
            n.lineno
            for n in ast.walk(v_tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("max", "min")
        ]
        self.assertEqual(maxmin, [])
        t_tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        self.assertEqual([n.lineno for n in ast.walk(t_tree) if isinstance(n, ast.Assert)], [])

    def test_bar_axis_ilss_shear_enforced_continuously_across_all_angles(self) -> None:
        F_ult = 180.449
        Ad_8 = 0.25 * math.pi * (8.0**2)
        phi_V_cap_12x8 = 12 * 0.60 * Ad_8 * 75.0 * 1e-3

        prev_v_bar = -1.0
        for th in (15.0, 30.0, 45.0, 60.0, 75.0, 85.0, 90.0):
            res = verify.evaluate_aci318_ch17_anchor_group(
                12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, th, 35.0,
                9.4, 13.5, F_ult, 0.0, True,
            )
            expected_v_bar = F_ult * math.sin(math.radians(th))
            self.assertAlmostEqual(res["V_bar_transverse_kN"], expected_v_bar, places=2)
            self.assertGreater(res["V_bar_transverse_kN"], prev_v_bar)
            self.assertAlmostEqual(res["phi_V_bar_shear_cap_kN"], phi_V_cap_12x8, places=2)
            expected_tau = (expected_v_bar * 1e3) / (12 * Ad_8)
            self.assertAlmostEqual(res["tau_bar_transverse_MPa"], expected_tau, places=1)
            # At every angle >= 15 deg, 180.45 * sin(theta) >= 46.7 kN > 27.14 kN brittle ILSS cap!
            self.assertFalse(res["gates"]["dowel_material_strength_and_interaction"], msg=f"theta={th} bypassed ILSS")
            self.assertFalse(res["all_ch17_gates_pass"], msg=f"theta={th} unexpectedly passed all gates")
            prev_v_bar = res["V_bar_transverse_kN"]

        # Verify continuity of Free Body B crack-plane shear V_bar across 88.9 -> 89.0 -> 90.0 deg
        # (Free Body A T_ult intentionally switches to pure-shear mode at theta >= 89 deg).
        r889 = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 88.9, 35.0, 9.4, 13.5, F_ult, 0.0, True,
        )
        r890 = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 89.0, 35.0, 9.4, 13.5, F_ult, 0.0, True,
        )
        self.assertLess(abs(r890["V_bar_transverse_kN"] - r889["V_bar_transverse_kN"]), 0.05)

    def test_single_counted_pad_friction_horizontal_equilibrium(self) -> None:
        F_ult = 180.449
        for th in (30.0, 45.0, 60.0, 75.0):
            for mu in (0.0, 0.25, 0.40, 0.60):
                res = verify.evaluate_aci318_ch17_anchor_group(
                    12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, th, 35.0,
                    9.4, 13.5, F_ult, 0.0, True, pad_friction_coeff=mu,
                )
                # Exact horizontal equilibrium: V_pry + mu * N_up == F_ult
                horiz_sum = res["V_pry_demand_kN"] + mu * res["N_ua_uplift_kN"]
                self.assertAlmostEqual(horiz_sum, F_ult, delta=0.03)
                # V_pry must stay strictly positive even when mu * tan(theta) > 1 (e.g. 75 deg, mu=0.60)
                self.assertGreater(res["V_pry_demand_kN"], 0.0)

        # At 45 deg, mu=0.40: V_pry = 128.89 kN (legacy double-counted formula gave 77.34 kN)
        r45 = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0, 9.4, 13.5, F_ult, 150.374, True,
        )
        self.assertAlmostEqual(r45["V_pry_demand_kN"], 128.89, places=2)
        # Conjugate Free Body B resultant matches F_ult exactly (no non-conjugate T_ult + V_bar mixing!)
        self.assertAlmostEqual(math.hypot(r45["N_bar_axial_kN"], r45["V_bar_transverse_kN"]), F_ult, delta=0.02)
        self.assertGreater(r45["interaction_5_3"], 1.0)
        self.assertFalse(r45["gates"]["tension_shear_interaction_17.8"])

    def test_honest_negative_findings_and_verified_steel_dowel_alternative(self) -> None:
        cert = verify.run_verification()
        ch17 = cert["aci318_ch17_dowel_and_epoxy_sizing_issue_2"]

        # 1. Bare unidirectional 45-deg CFRP dowels fail ILSS (and 12x8 also fails Ch. 17.8 interaction)
        dist_cfrp = ch17["distributed_post_yield_stitch_pair_8mm_bare_cfrp_ilss_fails"]
        end_cfrp = ch17["end_prestress_12x8mm_45deg_bare_cfrp_ilss_and_interaction_fail"]
        self.assertFalse(dist_cfrp["all_ch17_gates_pass"])
        self.assertFalse(dist_cfrp["gates"]["dowel_material_strength_and_interaction"])
        self.assertAlmostEqual(dist_cfrp["V_bar_transverse_kN"], 15.51, places=2)
        self.assertAlmostEqual(dist_cfrp["phi_V_bar_shear_cap_kN"], 4.52, places=2)
        self.assertAlmostEqual(dist_cfrp["dowel_material_interaction_5_3"], 7.8077, places=4)
        self.assertAlmostEqual(dist_cfrp["interaction_5_3"], 0.5616, places=4)

        self.assertFalse(end_cfrp["all_ch17_gates_pass"])
        self.assertFalse(end_cfrp["gates"]["dowel_material_strength_and_interaction"])
        self.assertFalse(end_cfrp["gates"]["tension_shear_interaction_17.8"])
        self.assertAlmostEqual(end_cfrp["interaction_5_3"], 1.0266, places=4)
        self.assertAlmostEqual(end_cfrp["V_bar_transverse_kN"], 127.60, places=2)
        self.assertAlmostEqual(end_cfrp["phi_V_bar_shear_cap_kN"], 27.14, places=2)
        self.assertAlmostEqual(end_cfrp["dowel_material_interaction_5_3"], 13.2067, places=4)

        # 2. ISO 898-1 Class 8.8 steel (12% < 14% elongation -> brittle ACI 318-19 §17.5.3 phi=0.65/0.60):
        #    14 x 8 mm FAILS R17.8 5/3 (1.0775 > 1.0) and §17.8.3 trilinear (1.3432 > 1.2)
        end_14_fail = ch17["end_prestress_14x8mm_45deg_class88_steel_brittle_interaction_fails"]
        self.assertEqual(end_14_fail["phi_N_bar"], 0.65)
        self.assertEqual(end_14_fail["phi_V_bar"], 0.60)
        self.assertFalse(end_14_fail["all_ch17_gates_pass"])
        self.assertFalse(end_14_fail["gates"]["dowel_material_strength_and_interaction"])
        self.assertAlmostEqual(end_14_fail["dowel_material_interaction_5_3"], 1.0775, places=4)
        self.assertAlmostEqual(end_14_fail["dowel_material_interaction_trilinear"], 1.3432, places=4)

        # 3. 16 x 8 mm (8-pair) end group + 2 x 8 mm stitches Class 8.8 steel pass ALL gates
        dist_st = ch17["distributed_post_yield_stitch_pair_8mm_class88_steel_passes"]
        end_st = ch17["end_prestress_16x8mm_45deg_class88_steel_stirrup_core_passes"]
        self.assertAlmostEqual(dist_st["A_se_bar_mm2"], 36.609, places=3)
        self.assertAlmostEqual(end_st["A_se_bar_mm2"], 36.609, places=3)
        self.assertEqual(dist_st["phi_N_bar"], 0.65)
        self.assertEqual(dist_st["phi_V_bar"], 0.60)
        self.assertTrue(dist_st["all_ch17_gates_pass"])
        self.assertAlmostEqual(dist_st["T_dowel_ult_group_kN"], 22.16, places=2)
        self.assertAlmostEqual(dist_st["phi_T_bar_tens_cap_kN"], 38.07, places=2)
        self.assertAlmostEqual(dist_st["V_bar_transverse_kN"], 15.51, places=2)
        self.assertAlmostEqual(dist_st["phi_V_bar_shear_cap_kN"], 21.09, places=2)
        self.assertAlmostEqual(dist_st["interaction_5_3"], 0.5616, places=4)
        self.assertAlmostEqual(dist_st["dowel_material_interaction_5_3"], 0.8236, places=4)
        self.assertAlmostEqual(dist_st["dowel_material_interaction_trilinear"], 1.1432, places=4)

        self.assertTrue(end_st["all_ch17_gates_pass"])
        self.assertEqual(end_st["n_anchors"], 16)
        self.assertAlmostEqual(end_st["phi_N_stirrup_core_kN"], 593.46, places=2)
        self.assertAlmostEqual(end_st["phi_Nag_bond_group_kN"], 260.37, places=2)
        self.assertAlmostEqual(end_st["T_dowel_ult_group_kN"], 182.28, places=2)
        self.assertAlmostEqual(end_st["phi_T_bar_tens_cap_kN"], 304.58, places=2)
        self.assertAlmostEqual(end_st["V_bar_transverse_kN"], 127.60, places=2)
        self.assertAlmostEqual(end_st["phi_V_bar_shear_cap_kN"], 168.69, places=2)
        self.assertAlmostEqual(end_st["interaction_5_3"], 0.6382, places=4)
        self.assertAlmostEqual(end_st["dowel_material_interaction_5_3"], 0.8625, places=4)
        self.assertAlmostEqual(end_st["dowel_material_interaction_trilinear"], 1.1753, places=4)

        # 4. Programmatic epoxy coupling: end anchor borehole count must equal 2 * n_anchors (32 end dowels)
        expected_ep = verify.size_dual_cartridge_epoxy(
            5600.0, 2, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 2 * end_st["n_anchors"], 230.0, 8.0, 12.0,
        )
        self.assertEqual(ch17["dual_cartridge_epoxy_6m_beam"], expected_ep)
        self.assertAlmostEqual(ch17["dual_cartridge_epoxy_6m_beam"]["total_mixed_epoxy_mL"], 2421.3, places=1)
        self.assertEqual(ch17["dual_cartridge_epoxy_6m_beam"]["dual_cartridges_per_6m_beam"], 5)

        # 5. All 15 must-fail controls are live and rejected
        self.assertEqual(len(cert["must_fail_controls"]), 15)
        self.assertTrue(all(c["rejected"] for c in cert["must_fail_controls"]))

    def test_domain_and_material_guards_fail_closed(self) -> None:
        base_args = [12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0, 9.4, 13.5, 180.0, 150.0, True]
        for bad_theta in (0.0, -5.0, 90.01, 135.0, float("nan"), float("inf")):
            args = list(base_args)
            args[8] = bad_theta
            with self.assertRaises(ValueError):
                verify.evaluate_aci318_ch17_anchor_group(*args)
        for bad_kw in (
            {"pad_friction_coeff": -0.01},
            {"pad_friction_coeff": float("nan")},
            {"f_dowel_tens_MPa": 0.0},
            {"f_dowel_tens_MPa": -100.0},
            {"f_dowel_tens_MPa": float("nan")},
            {"tau_dowel_shear_MPa": 0.0},
            {"tau_dowel_shear_MPa": -10.0},
            {"tau_dowel_shear_MPa": float("inf")},
            {"A_se_ratio": 0.0},
            {"A_se_ratio": -0.5},
            {"A_se_ratio": 1.01},
            {"A_se_ratio": float("nan")},
            {"phi_N_bar": 0.0},
            {"phi_N_bar": -0.65},
            {"phi_N_bar": 1.05},
            {"phi_N_bar": float("nan")},
            {"phi_V_bar": 0.0},
            {"phi_V_bar": -0.60},
            {"phi_V_bar": 1.05},
            {"phi_V_bar": float("inf")},
        ):
            with self.assertRaises(ValueError):
                verify.evaluate_aci318_ch17_anchor_group(*base_args, **bad_kw)
        for bad_load_idx in (12, 13):
            args = list(base_args)
            args[bad_load_idx] = -1.0
            with self.assertRaises(ValueError):
                verify.evaluate_aci318_ch17_anchor_group(*args)


if __name__ == "__main__":
    unittest.main()
