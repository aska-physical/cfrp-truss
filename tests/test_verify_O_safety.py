"""R5 -O-safety and fail-closed regression tests for aska-physical/cfrp-truss.

Zero bare ``assert`` statements; runs cleanly across ``-B``, ``-O -B``, ``-B -W error``,
and isolated ``-I`` modes (when ``sympy`` is available in the interpreter environment).
"""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import verify


class TestCfrpTrussOSafety(unittest.TestCase):
    def test_zero_bare_asserts_in_verify_and_tests(self) -> None:
        for rel in ("verify.py", "tests/test_verify_O_safety.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            assert_lines = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
            self.assertEqual(assert_lines, [], f"unexpected bare assert in {rel}: {assert_lines}")

    def test_require_helper_fails_closed_under_optimize(self) -> None:
        verify.require(True, "should pass")
        with self.assertRaises(ValueError) as ctx:
            verify.require(False, "planted failure")
        self.assertIn("planted failure", str(ctx.exception))

    def test_baseline_certificate_matches_exactly_and_main_passes(self) -> None:
        expected = json.loads((ROOT / "results" / "certificate.json").read_text(encoding="utf-8"))
        fresh = verify.run_verification()
        self.assertEqual(fresh, expected)
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["verify.py"]), redirect_stdout(buf):
            rc = verify.main()
        self.assertEqual(rc, 0)
        self.assertIn("(15/15 controls): OK", buf.getvalue())

    def test_every_symbolic_identity_guard_and_wrapper_is_live(self) -> None:
        real_simplify = verify.sp.simplify
        for fail_idx in (1, 2, 3, 4, 5):
            calls = [0]

            def _sabotage(expr: object, _idx: int = fail_idx) -> object:
                calls[0] += 1
                if calls[0] == _idx:
                    return verify.sp.Integer(1)
                return real_simplify(expr)

            with mock.patch.object(verify.sp, "simplify", side_effect=_sabotage):
                with self.assertRaises(ValueError, msg=f"symbolic guard #{fail_idx} must fail closed"):
                    verify.verify_symbolic_identities()

        with mock.patch.object(verify, "verify_symbolic_identities", return_value=False):
            with self.assertRaises(ValueError):
                verify.run_verification()

    def test_solver_residual_guard_fails_closed_on_non_convergent_moment(self) -> None:
        # 1 mm compression width cannot balance 1500 mm^2 steel inside ck in [5, 0.92*d],
        # forcing a large unconverged force equilibrium residual (|f_res| >> 1.0 N).
        with self.assertRaises(ValueError) as ctx:
            verify.solve_cracked_serviceability(
                175.0, 91.0, 1.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 4018.74, 0.00047
            )
        self.assertIn("did not converge", str(ctx.exception))

    def test_all_run_verification_gates_are_independently_live(self) -> None:
        real_beam = verify.solve_rc_cfrp_beam
        real_serv = verify.solve_cracked_serviceability

        # 1. Fail tr_u > 2.5 * ebr_u while keeping truss_120 > ebr_240
        beam_calls = [0]

        def _low_tr_u(*args: object, **kwargs: object) -> dict[str, float]:
            beam_calls[0] += 1
            res = real_beam(*args, **kwargs)  # type: ignore[arg-type]
            if beam_calls[0] == 3:
                res = dict(res, eps_fe=0.0001)
            return res

        with mock.patch.object(verify, "solve_rc_cfrp_beam", side_effect=_low_tr_u):
            with self.assertRaises(ValueError) as ctx1:
                verify.run_verification()
            self.assertIn("2.5x EBR", str(ctx1.exception))

        # 2. Fail truss_120 > ebr_240 while keeping tr_u > 2.5 * ebr_u
        beam_calls2 = [0]

        def _low_truss_120(*args: object, **kwargs: object) -> dict[str, float]:
            beam_calls2[0] += 1
            res = real_beam(*args, **kwargs)  # type: ignore[arg-type]
            if beam_calls2[0] == 4:
                res = dict(res, phi_Mn_kNm=100.0)
            return res

        with mock.patch.object(verify, "solve_rc_cfrp_beam", side_effect=_low_truss_120):
            with self.assertRaises(ValueError) as ctx2:
                verify.run_verification()
            self.assertIn("half-mass truss capacity", str(ctx2.exception))

        # 3. Fail s_des serviceability gate
        serv_calls = [0]

        def _fail_s_des(*args: object, **kwargs: object) -> dict[str, object]:
            serv_calls[0] += 1
            res = real_serv(*args, **kwargs)  # type: ignore[arg-type]
            if serv_calls[0] == 1:
                res = dict(res, all_serviceability_gates_pass=False)
            return res

        with mock.patch.object(verify, "solve_cracked_serviceability", side_effect=_fail_s_des):
            with self.assertRaises(ValueError) as ctx3:
                verify.run_verification()
            self.assertIn("design serviceability", str(ctx3.exception))

        # 4. Fail s_ov_pr serviceability gate
        serv_calls2 = [0]

        def _fail_s_ov_pr(*args: object, **kwargs: object) -> dict[str, object]:
            serv_calls2[0] += 1
            res = real_serv(*args, **kwargs)  # type: ignore[arg-type]
            if serv_calls2[0] == 3:
                res = dict(res, all_serviceability_gates_pass=False)
            return res

        with mock.patch.object(verify, "solve_cracked_serviceability", side_effect=_fail_s_ov_pr):
            with self.assertRaises(ValueError) as ctx4:
                verify.run_verification()
            self.assertIn("prestressed overload serviceability", str(ctx4.exception))

        # 5. Fail a must-fail control while all earlier gates pass
        with mock.patch.object(
            verify, "shear_stitch_contribution_kN", return_value={"eps_fv_cap": 0.01, "Vf_45deg_kN": 177.218}
        ):
            with self.assertRaises(ValueError) as ctx5:
                verify.run_verification()
            self.assertIn("must-fail controls not rejected", str(ctx5.exception))

    def test_tampered_strain_cap_fails_closed_under_O_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "repo"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            vp = dst / "verify.py"
            vp.write_text(
                vp.read_text(encoding="utf-8").replace("0.70 * eps_fu", "0.10 * eps_fu"),
                encoding="utf-8",
            )
            cert_path = dst / "results" / "certificate.json"
            before_bytes = cert_path.read_bytes()
            proc = subprocess.run(
                [sys.executable, "-I", "-O", "-B", str(vp), "--write"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0, "tampered verify.py --write passed under -I -O -B")
            self.assertIn("ValueError", proc.stderr)
            self.assertEqual(cert_path.read_bytes(), before_bytes)

    def test_empty_1ppm_perturbed_and_missing_certificate_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cert_path = Path(tmp) / "certificate.json"

            # Empty receipt {}
            cert_path.write_text("{}\n", encoding="utf-8")
            err_empty = io.StringIO()
            with (
                mock.patch.object(verify, "CERT_PATH", cert_path),
                mock.patch.object(sys, "argv", ["verify.py"]),
                redirect_stderr(err_empty),
            ):
                self.assertEqual(verify.main(), 1)
            self.assertIn("[FAIL]", err_empty.getvalue())

            # 1-ppm-style scalar perturbation
            orig = json.loads((ROOT / "results" / "certificate.json").read_text(encoding="utf-8"))
            orig["beam_benchmark_SI"]["truss_anchored_240mm2_phi_Mn_kNm"] += 0.001
            cert_path.write_text(json.dumps(orig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            err_pert = io.StringIO()
            with (
                mock.patch.object(verify, "CERT_PATH", cert_path),
                mock.patch.object(sys, "argv", ["verify.py"]),
                redirect_stderr(err_pert),
            ):
                self.assertEqual(verify.main(), 1)

            # Missing certificate
            cert_path.unlink()
            err_missing = io.StringIO()
            with (
                mock.patch.object(verify, "CERT_PATH", cert_path),
                mock.patch.object(sys, "argv", ["verify.py"]),
                redirect_stderr(err_missing),
            ):
                self.assertEqual(verify.main(), 1)

            # Clean --write writes exact matching certificate
            out_w = io.StringIO()
            with (
                mock.patch.object(verify, "CERT_PATH", cert_path),
                mock.patch.object(sys, "argv", ["verify.py", "--write"]),
                redirect_stdout(out_w),
            ):
                self.assertEqual(verify.main(), 0)
            self.assertEqual(
                cert_path.read_text(encoding="utf-8"),
                (ROOT / "results" / "certificate.json").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
