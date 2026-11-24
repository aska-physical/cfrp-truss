#!/usr/bin/env python3
"""Zero-bare-assert regression suite for NaN/Inf fail-closed guards and atomic dir_fd cert I/O.

Covers:
  - NaN-swallowing regressions (`V_pry=nan`, `beta1(fc=nan)`).
  - Full positive-input, exact-type, `10**4300` overflow, and exact-message positive-domain
    regression coverage across every solver in `verify.py`.
  - Atomic `dir_fd` certificate I/O (`0o600` temp mode, `st_nlink <= 1` hardlink
    refusal, pre-replace temp `st_nlink != 1` refusal, `EEXIST` retry/exhaustion, `16T` concurrent
    distinct/same-target writes, streaming `max_bytes` cap, deleted-CWD refusal, raw `..` and
    logical-`PWD` symlink refusal, lone UTF-16 surrogates, NUL bytes, FIFOs, directories, CRLF).
"""

from __future__ import annotations

import ast
from contextlib import redirect_stderr
import errno
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import verify


class _PathSub(type(Path("."))):
    pass


class _IntSub(int):
    pass


class _BytesSub(bytes):
    pass


class TestCfrpTrussNanAndZoneBHardening(unittest.TestCase):
    def test_zero_bare_asserts_and_zero_builtin_maxmin_in_verify(self) -> None:
        tree = ast.parse((ROOT / "verify.py").read_text(encoding="utf-8"))
        asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
        self.assertEqual(asserts, [])
        maxmin = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in ("max", "min")
        ]
        self.assertEqual(maxmin, [])
        test_tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        self.assertEqual([n.lineno for n in ast.walk(test_tree) if isinstance(n, ast.Assert)], [])

    def test_finite_primitives_and_boundaries(self) -> None:
        self.assertEqual(verify.require_finite(3, "x"), 3.0)
        for bad in (True, False, _IntSub(1), "1.0", None, float("nan"), float("inf"), float("-inf"), 10**400, 10**4300, -(10**4300)):
            with self.assertRaises(ValueError):
                verify.require_finite(bad, "x")

        self.assertEqual(verify.require_positive_int(1, "n"), 1)
        for bad_n in (True, False, _IntSub(1), 0, -1, 1.5, "2", None, 10**400, 10**4300):
            with self.assertRaises(ValueError):
                verify.require_positive_int(bad_n, "n")

        self.assertEqual(verify.finite_min(1.0, 2.0), 1.0)
        self.assertEqual(verify.finite_min(3.0, 2.0), 2.0)
        self.assertEqual(verify.finite_max(1.0, 2.0), 2.0)
        self.assertEqual(verify.finite_max(3.0, 2.0), 3.0)
        for fn in (verify.finite_min, verify.finite_max):
            with self.assertRaises(ValueError):
                fn(0.0, float("nan"))
            with self.assertRaises(ValueError):
                fn(float("nan"), 0.0)

        self.assertEqual(verify.finite_clamp(0.75, 0.65, 0.85), 0.75)
        self.assertEqual(verify.finite_clamp(0.50, 0.65, 0.85), 0.65)
        self.assertEqual(verify.finite_clamp(0.95, 0.65, 0.85), 0.85)
        self.assertEqual(verify.finite_clamp(0.65, 0.65, 0.65), 0.65)
        with self.assertRaises(ValueError):
            verify.finite_clamp(0.75, 0.85, 0.65)
        with self.assertRaises(ValueError):
            verify.finite_clamp(float("nan"), 0.65, 0.85)

        self.assertEqual(verify.finite_max_seq([1.0, 5.0, 3.0]), 5.0)
        self.assertEqual(verify.finite_max_seq([2.0]), 2.0)
        with self.assertRaises(ValueError):
            verify.finite_max_seq([])
        with self.assertRaises(ValueError):
            verify.finite_max_seq([1.0, float("nan")])

    def test_headline_nan_swallowing_and_solver_guards_fail_closed(self) -> None:
        # 1. NaN-swallowing regression #1: V_pry=nan in evaluate_aci318_ch17_anchor_group (theta=90 deg)
        with self.assertRaises(ValueError):
            verify.evaluate_aci318_ch17_anchor_group(
                12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 90.0, 35.0,
                9.4, 13.5, float("nan"), 0.0, True,
            )
        with self.assertRaises(ValueError):
            verify.evaluate_aci318_ch17_anchor_group(
                12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, float("nan"),
                9.4, 13.5, 180.0, 150.0, True,
            )
        # Kill every subcondition of L453 (fc > 0 and da > 0 and L_drill > 0 and stirrup_spacing > 0)
        for bad_kw in (
            (8.0, 230.0, 0.0, 150.0),
            (0.0, 230.0, 35.0, 150.0),
            (8.0, 0.0, 35.0, 150.0),
            (8.0, 230.0, 35.0, 0.0),
        ):
            da_v, ldr_v, fc_v, sp_v = bad_kw
            with self.assertRaises(ValueError) as ctx:
                verify.evaluate_aci318_ch17_anchor_group(
                    12, 2, 220.0, 140.0, 200.0, 300.0, da_v, ldr_v, 45.0, fc_v,
                    9.4, 13.5, 180.0, 150.0, True, stirrup_spacing_mm=sp_v,
                )
            self.assertIn("anchor geometry/strength inputs must be > 0", str(ctx.exception))

        # Exercise engage_closed_stirrup_core=False with hef_geom >= 120.0 AND True with hef_geom < 120.0
        cover_only = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0,
            9.4, 13.5, 180.0, 150.0, False,
        )
        self.assertEqual(cover_only["phi_N_stirrup_core_kN"], 0.0)
        self.assertLess(cover_only["phi_Vcpg_pryout_kN"], 200.0)
        shallow_engaged = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 150.0, 45.0, 35.0,
            25.0, 13.5, 180.0, 150.0, True,
        )
        shallow_cover = verify.evaluate_aci318_ch17_anchor_group(
            12, 2, 220.0, 140.0, 200.0, 300.0, 8.0, 150.0, 45.0, 35.0,
            25.0, 13.5, 180.0, 150.0, False,
        )
        self.assertEqual(shallow_engaged["phi_N_stirrup_core_kN"], 0.0)
        self.assertLess(shallow_engaged["phi_Ncbg_unreinforced_kN"], shallow_engaged["phi_Nag_bond_group_kN"])
        self.assertEqual(shallow_engaged["phi_Vcpg_pryout_kN"], shallow_cover["phi_Vcpg_pryout_kN"])

        # 2. NaN-swallowing regression #2: beta1(fc_MPa=nan) + every subcondition of beam geometry/strength guard
        with self.assertRaises(ValueError):
            verify.solve_rc_cfrp_beam(300.0, 520.0, 600.0, float("nan"), 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.01)
        for bad_beam in (
            (300.0, 520.0, 600.0, 0.0),
            (0.0, 520.0, 600.0, 35.0),
            (300.0, 0.0, 600.0, 35.0),
            (300.0, 520.0, 0.0, 35.0),
        ):
            b_v, d_v, h_v, fc_v = bad_beam
            with self.assertRaises(ValueError) as ctx:
                verify.solve_rc_cfrp_beam(b_v, d_v, h_v, fc_v, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.01)
            self.assertIn("beam geometry/strength inputs must be > 0", str(ctx.exception))

        # 3. aci440_ebr_debonding_strain — test all 4 positive-domain subconditions individually
        with self.assertRaises(ValueError):
            verify.aci440_ebr_debonding_strain(float("nan"), 2, 195800.0, 1.2, 0.02)
        with self.assertRaises(ValueError):
            verify.aci440_ebr_debonding_strain(35.0, 0, 195800.0, 1.2, 0.02)
        for bad_ebr in (
            (0.0, 2, 195800.0, 1.2, 0.02),
            (35.0, 2, 0.0, 1.2, 0.02),
            (35.0, 2, 195800.0, 0.0, 0.02),
            (35.0, 2, 195800.0, 1.2, 0.0),
        ):
            with self.assertRaises(ValueError) as ctx:
                verify.aci440_ebr_debonding_strain(*bad_ebr)
            self.assertIn("EBR debonding inputs must be > 0", str(ctx.exception))

        # Every subcondition of solve_unstrengthened_cracked_eps_bi L188
        for bad_cr in (
            (0.0, 520.0, 35.0, 1500.0, 200000.0),
            (300.0, 0.0, 35.0, 1500.0, 200000.0),
            (300.0, 520.0, 0.0, 1500.0, 200000.0),
            (300.0, 520.0, 35.0, 0.0, 200000.0),
            (300.0, 520.0, 35.0, 1500.0, 0.0),
        ):
            b_v, d_v, fc_v, as_v, es_v = bad_cr
            with self.assertRaises(ValueError) as ctx:
                verify.solve_unstrengthened_cracked_eps_bi(55.0, b_v, d_v, 590.0, fc_v, as_v, es_v)
            self.assertIn("cracked section inputs must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.solve_unstrengthened_cracked_eps_bi(float("nan"), 300.0, 520.0, 590.0, 35.0, 1500.0, 200000.0)

        # Every subcondition of solve_cracked_serviceability L212
        for bad_sv in (
            (300.0, 520.0, 0.0),
            (0.0, 520.0, 35.0),
            (300.0, 0.0, 35.0),
        ):
            b_v, d_v, fc_v = bad_sv
            with self.assertRaises(ValueError) as ctx:
                verify.solve_cracked_serviceability(175.0, 91.0, b_v, d_v, 590.0, fc_v, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 4018.74, 0.00047)
            self.assertIn("serviceability section inputs must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.solve_cracked_serviceability(float("nan"), 91.0, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 4018.74, 0.00047)

        # solve_chord_shear_flow_fd: positive ints + both subconditions of L370
        for bad_steps in (0, -5, True, 2.5, 10**400):
            with self.assertRaises(ValueError):
                verify.solve_chord_shear_flow_fd(481.61, 2000.0, bad_steps, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.00047, 0.0032)
        for bad_grooves in (0, -1, True, 1.5, 10**400):
            with self.assertRaises(ValueError):
                verify.solve_chord_shear_flow_fd(481.61, 2000.0, 10, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.00047, 0.0032, n_grooves=bad_grooves)
        for bad_l, bad_fc in ((0.0, 35.0), (2000.0, 0.0)):
            with self.assertRaises(ValueError) as ctx:
                verify.solve_chord_shear_flow_fd(481.61, bad_l, 10, 300.0, 520.0, 590.0, bad_fc, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.00047, 0.0032)
            self.assertIn("chord shear flow inputs must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.solve_chord_shear_flow_fd(float("nan"), 2000.0, 10, 300.0, 520.0, 590.0, 35.0, 1500.0, 420.0, 200000.0, 240.0, 195800.0, 0.00047, 0.0032)

        # Integer count guards on evaluate_aci318_ch17_anchor_group
        for bad_a, bad_r in ((True, 2), (0, 2), (10**400, 2), (12, True), (12, 0), (12, 10**400), (1, 2)):
            with self.assertRaises(ValueError):
                verify.evaluate_aci318_ch17_anchor_group(
                    bad_a, bad_r, 220.0, 140.0, 200.0, 300.0, 8.0, 230.0, 45.0, 35.0,
                    9.4, 13.5, 180.0, 150.0, True,
                )

        # 4. size_dual_cartridge_epoxy, shear_stitch_contribution_kN, & cole_bondline_eis exact messages
        with self.assertRaises(ValueError) as ctx:
            verify.size_dual_cartridge_epoxy(5600.0, 2, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 24, 230.0, 8.0, 12.0, cartridge_vol_mL=0.0)
        self.assertIn("cartridge_vol_mL must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.size_dual_cartridge_epoxy(float("nan"), 2, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 24, 230.0, 8.0, 12.0)
        with self.assertRaises(ValueError):
            verify.size_dual_cartridge_epoxy(1e308, 2, 1e308, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 24, 230.0, 8.0, 12.0)
        with self.assertRaises(ValueError):
            verify.size_dual_cartridge_epoxy(5600.0, 2, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 1e308, 24, 230.0, 8.0, 12.0)
        with self.assertRaises(ValueError):
            verify.size_dual_cartridge_epoxy(5600.0, True, 12.0, 20.0, 240.0, 18, 226.0, 8.0, 12.0, 24, 230.0, 8.0, 12.0)

        with self.assertRaises(ValueError) as ctx:
            verify.shear_stitch_contribution_kN(50.0, 195800.0, 480.0, 0.0)
        self.assertIn("s_mm must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.shear_stitch_contribution_kN(float("nan"), 195800.0, 480.0, 150.0)
        with self.assertRaises(ValueError):
            verify.shear_stitch_contribution_kN(1e308, 1e308, 480.0, 150.0)

        # Match exact error messages so cmath.log(0j) never masks tau_s/freqs_Hz/omega_tau guard checks
        for bad_tau in (0.0, -1.0):
            with self.assertRaises(ValueError) as ctx:
                verify.cole_bondline_eis([100.0], 12.0, 420.0, bad_tau, 0.75)
            self.assertIn("tau_s must be > 0", str(ctx.exception))
        for bad_f in (0.0, -5.0):
            with self.assertRaises(ValueError) as ctx:
                verify.cole_bondline_eis([bad_f], 12.0, 420.0, 1.5e-6, 0.75)
            self.assertIn("freqs_Hz entries must be > 0", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.cole_bondline_eis([float("nan")], 12.0, 420.0, 1.5e-6, 0.75)
        for bad_alpha in (0.0, -0.5, 1.01, 1e308):
            with self.assertRaises(ValueError) as ctx:
                verify.cole_bondline_eis([100.0], 12.0, 420.0, 1.5e-6, bad_alpha)
            self.assertIn("alpha must be in (0, 1]", str(ctx.exception))
        self.assertEqual(len(verify.cole_bondline_eis([100.0], 12.0, 420.0, 1.5e-6, 1.0)), 1)
        with self.assertRaises(ValueError) as ctx:
            verify.cole_bondline_eis([1e308], 12.0, 420.0, 1e308, 0.75)
        self.assertIn("omega_tau", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            verify.cole_bondline_eis([1e-300], 12.0, 420.0, 1e-300, 0.75)
        self.assertIn("omega_tau must be > 0 without underflow", str(ctx.exception))
        with self.assertRaises(ValueError):
            verify.cole_bondline_eis([100.0], -1e308, 1e308, 1.5e-6, 0.75)

    def test_zone_b_canonical_cert_verification_and_atomic_write_guards(self) -> None:
        cert_bytes = (ROOT / "results" / "certificate.json").read_bytes()
        cert_obj = json.loads(cert_bytes.decode("utf-8"))
        self.assertEqual(verify.render_certificate_bytes(cert_obj), cert_bytes)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = root / "certificate.json"
            good.write_bytes(cert_bytes)

            ok, reason = verify.verify_canonical_cert_bytes(good, cert_bytes, max_bytes=len(cert_bytes))
            self.assertTrue(ok, reason)
            ok_over, reason_over = verify.verify_canonical_cert_bytes(good, cert_bytes, max_bytes=len(cert_bytes) - 1)
            self.assertFalse(ok_over)
            self.assertIn("exceeds", reason_over)
            ok_cap, r_cap = verify.verify_canonical_cert_bytes(good, cert_bytes, max_bytes=verify.HARD_MAX_BYTES_CAP)
            self.assertTrue(ok_cap, r_cap)
            self.assertFalse(verify.verify_canonical_cert_bytes(good, cert_bytes, max_bytes=verify.HARD_MAX_BYTES_CAP + 1)[0])
            self.assertFalse(verify.verify_canonical_cert_bytes(good, cert_bytes, max_bytes=0)[0])
            self.assertFalse(verify.verify_canonical_cert_bytes(good, _BytesSub(cert_bytes))[0])
            self.assertFalse(verify.write_canonical_cert_bytes(good, _BytesSub(cert_bytes))[0])

            # Malformed certs: bool-as-int, duplicate key, int-as-float, CRLF, 5000-digit, invalid UTF-8/truncated
            for name, payload_b in (
                ("zb06.json", cert_bytes.replace(b"true", b"1", 1)),
                ("zb07.json", cert_bytes.replace(b'"certificate_version": "0.3.0",', b'"certificate_version": "0.0.0",\n  "certificate_version": "0.3.0",', 1)),
                ("zb08.json", cert_bytes.replace(b'"n_anchors": 2,', b'"n_anchors": 2.0,', 1)),
                ("zb03.json", ('{"x": ' + "9" * 5000 + "}\n").encode("utf-8")),
                ("zb16_utf8.json", b"\xff\xfe\x00\n"),
                ("zb16_trunc.json", b'{"a": '),
            ):
                p = root / name
                p.write_bytes(payload_b)
                self.assertFalse(verify.verify_canonical_cert_bytes(p, cert_bytes)[0])

            zb10 = root / "zb10.json"
            zb10.write_bytes(cert_bytes.replace(b"\n", b"\r\n"))
            ok_crlf, reason_crlf = verify.verify_canonical_cert_bytes(zb10, cert_bytes)
            self.assertFalse(ok_crlf)
            self.assertIn("CRLF", reason_crlf)

            # Symlink target, symlink ancestor, raw .. symlink, relative PWD symlink, Path subclass, NUL, surrogate
            sym = root / "sym.json"
            sym.symlink_to(good)
            ok_sym, r_sym = verify.verify_canonical_cert_bytes(sym, cert_bytes)
            self.assertFalse(ok_sym)
            self.assertIn("symlink", r_sym)
            ok_sym_w, r_sym_w = verify.write_canonical_cert_bytes(sym, cert_bytes)
            self.assertFalse(ok_sym_w)
            self.assertIn("symlink", r_sym_w)

            real_dir = root / "real_dir"
            real_dir.mkdir()
            (real_dir / "c.json").write_bytes(cert_bytes)
            sym_dir = root / "sym_dir"
            sym_dir.symlink_to(real_dir)
            ok_sd, r_sd = verify.verify_canonical_cert_bytes(sym_dir / "c.json", cert_bytes)
            self.assertFalse(ok_sd)
            self.assertIn("symlink forbidden", r_sd)
            ok_sd_dot, r_sd_dot = verify.verify_canonical_cert_bytes(Path(str(sym_dir) + "/../certificate.json"), cert_bytes)
            self.assertFalse(ok_sd_dot)
            self.assertIn("symlink forbidden", r_sd_dot)
            ok_sd_w, r_sd_w = verify.write_canonical_cert_bytes(sym_dir / "c.json", cert_bytes)
            self.assertFalse(ok_sd_w)
            self.assertIn("symlink forbidden", r_sd_w)

            # Non-directory ancestor & root / . / .. / missing parent
            ok_nd, r_nd = verify.verify_canonical_cert_bytes(good / "child.json", cert_bytes)
            self.assertFalse(ok_nd)
            self.assertIn("non-directory ancestor", r_nd)
            for dir_leaf in (Path("."), Path(".."), Path("/")):
                with self.assertRaises(ValueError):
                    verify._open_parent_dir_fd(dir_leaf)
            self.assertFalse(verify.verify_canonical_cert_bytes(root / "no_dir" / "c.json", cert_bytes)[0])

            old_cwd = os.getcwd()
            old_pwd = os.environ.get("PWD")
            try:
                os.chdir(real_dir)
                os.environ["PWD"] = str(sym_dir)
                self.assertFalse(verify.verify_canonical_cert_bytes(Path("c.json"), cert_bytes)[0])
                os.environ["PWD"] = str(real_dir)
                self.assertTrue(verify.verify_canonical_cert_bytes(Path("c.json"), cert_bytes)[0])
                os.environ["PWD"] = "relative_pwd"
                self.assertTrue(verify.verify_canonical_cert_bytes(Path("c.json"), cert_bytes)[0])
                os.environ["PWD"] = ""
                self.assertTrue(verify.verify_canonical_cert_bytes(Path("c.json"), cert_bytes)[0])
            finally:
                os.chdir(old_cwd)
                if old_pwd is None:
                    os.environ.pop("PWD", None)
                else:
                    os.environ["PWD"] = old_pwd

            self.assertFalse(verify.verify_canonical_cert_bytes(_PathSub(good), cert_bytes)[0])
            self.assertFalse(verify.verify_canonical_cert_bytes(Path("a\x00b"), cert_bytes)[0])
            self.assertFalse(verify.verify_canonical_cert_bytes(Path(str(root) + "/\ud800"), cert_bytes)[0])

            # Directory, FIFO, hardlink refusal, 0o600 mode, EEXIST retry/exhaustion, pre-replace nlink!=1
            d_target = root / "dir_cert"
            d_target.mkdir()
            ok_dir, r_dir = verify.verify_canonical_cert_bytes(d_target, cert_bytes)
            self.assertFalse(ok_dir)
            self.assertIn("regular file", r_dir)
            self.assertFalse(verify.write_canonical_cert_bytes(d_target, cert_bytes)[0])

            fifo = root / "fifo.json"
            os.mkfifo(fifo)
            ok_fifo_r, r_fifo_r = verify.verify_canonical_cert_bytes(fifo, cert_bytes)
            self.assertFalse(ok_fifo_r)
            self.assertIn("regular file", r_fifo_r)
            ok_fifo_w, r_fifo_w = verify.write_canonical_cert_bytes(fifo, cert_bytes)
            self.assertFalse(ok_fifo_w)
            self.assertIn("non-regular target", r_fifo_w)

            hl_a = root / "hl_a.json"
            hl_a.write_bytes(cert_bytes)
            os.link(hl_a, root / "hl_b.json")
            ok_hl, r_hl = verify.write_canonical_cert_bytes(hl_a, cert_bytes)
            self.assertFalse(ok_hl)
            self.assertIn("hardlinked", r_hl)

            self.assertFalse(verify.verify_canonical_cert_bytes(root / "missing.json", cert_bytes)[0])
            fresh = root / "sub" / "fresh.json"
            self.assertTrue(verify.write_canonical_cert_bytes(fresh, cert_bytes)[0])
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o600)
            self.assertTrue(verify.verify_canonical_cert_bytes(fresh, cert_bytes)[0])

            # Simulated pre-replace temp st_nlink != 1, EEXIST retry & exhaustion, deleted CWD, os.read OSError
            real_fstat = os.fstat

            def _fake_nlink2(fd: int) -> os.stat_result:
                s = real_fstat(fd)
                return os.stat_result((s.st_mode, s.st_ino, s.st_dev, 2, s.st_uid, s.st_gid, s.st_size, s.st_atime, s.st_mtime, s.st_ctime))

            with mock.patch("os.fstat", side_effect=_fake_nlink2):
                ok_tn, r_tn = verify.write_canonical_cert_bytes(root / "tmp_hl.json", cert_bytes)
                self.assertFalse(ok_tn)
                self.assertIn("hardlinked before replace", r_tn)

            real_open = os.open
            once = [0]

            def _eexist_once(p_arg, flags, *a, **kw):
                if (flags & os.O_CREAT) and once[0] == 0:
                    once[0] += 1
                    raise FileExistsError("collide")
                return real_open(p_arg, flags, *a, **kw)

            with mock.patch("os.open", side_effect=_eexist_once):
                self.assertTrue(verify.write_canonical_cert_bytes(root / "retry_ok.json", cert_bytes)[0])

            def _eexist_all(p_arg, flags, *a, **kw):
                if flags & os.O_CREAT:
                    raise FileExistsError("always")
                return real_open(p_arg, flags, *a, **kw)

            with mock.patch("os.open", side_effect=_eexist_all):
                ok_ex, r_ex = verify.write_canonical_cert_bytes(root / "retry_fail.json", cert_bytes)
                self.assertFalse(ok_ex)
                self.assertIn("exhausted", r_ex)

            with mock.patch("os.getcwd", side_effect=FileNotFoundError("deleted")):
                self.assertFalse(verify.verify_canonical_cert_bytes(Path("rel.json"), cert_bytes)[0])
            with mock.patch("os.read", side_effect=OSError(errno.EIO, "io")):
                self.assertFalse(verify.verify_canonical_cert_bytes(good, cert_bytes)[0])
            with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "full")):
                self.assertFalse(verify.write_canonical_cert_bytes(root / "full.json", cert_bytes)[0])

            # Concurrent 16T distinct & 16T same-target atomic writes (0 errs, 0o600, 0 leftover tmps)
            cdir = root / "conc"
            cdir.mkdir()
            errs: list[str] = []
            b1 = threading.Barrier(16)

            def _w_dist(idx: int) -> None:
                b1.wait()
                ok_i, r_i = verify.write_canonical_cert_bytes(cdir / f"d_{idx}.json", cert_bytes)
                if not ok_i:
                    errs.append(r_i)

            ts1 = [threading.Thread(target=_w_dist, args=(i,)) for i in range(16)]
            for t in ts1:
                t.start()
            for t in ts1:
                t.join()
            self.assertEqual(errs, [])

            shared = cdir / "shared.json"
            b2 = threading.Barrier(16)

            def _w_same(_idx: int) -> None:
                b2.wait()
                ok_i, r_i = verify.write_canonical_cert_bytes(shared, cert_bytes)
                if not ok_i:
                    errs.append(r_i)

            ts2 = [threading.Thread(target=_w_same, args=(i,)) for i in range(16)]
            for t in ts2:
                t.start()
            for t in ts2:
                t.join()
            self.assertEqual(errs, [])
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o600)
            self.assertEqual([f.name for f in cdir.iterdir() if f.name.startswith(".cfrp_truss_tmp_")], [])

            # main() --write refused on symlink & main() Zone-B reject returns rc=1 with zero traceback
            err_buf = io.StringIO()
            with (
                mock.patch.object(verify, "CERT_PATH", sym),
                mock.patch.object(verify, "run_verification", return_value=cert_obj),
                mock.patch.object(sys, "argv", ["verify.py", "--write"]),
                redirect_stderr(err_buf),
            ):
                rc = verify.main()
            self.assertEqual(rc, 1)
            self.assertIn("write refused", err_buf.getvalue())

            for bad_path in (zb10, sym, d_target, fifo):
                eb = io.StringIO()
                with (
                    mock.patch.object(verify, "CERT_PATH", bad_path),
                    mock.patch.object(verify, "run_verification", return_value=cert_obj),
                    mock.patch.object(sys, "argv", ["verify.py"]),
                    redirect_stderr(eb),
                ):
                    self.assertEqual(verify.main(), 1, msg=f"bad_path={bad_path.name} passed")
                self.assertIn("[FAIL]", eb.getvalue())

    def test_live_subprocess_symlink_and_crlf_fail_closed_under_O(self) -> None:
        orig_bytes = (ROOT / "results" / "certificate.json").read_bytes()
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            cert_path = work / "results" / "certificate.json"
            cert_path.write_bytes(orig_bytes.replace(b"\n", b"\r\n"))
            p = subprocess.run([sys.executable, "-I", "-O", "-B", "verify.py"], cwd=work, text=True, capture_output=True, check=False)
            self.assertEqual(p.returncode, 1)
            self.assertIn("[FAIL]", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_darwin_system_root_alias_normalization(self) -> None:
        from types import SimpleNamespace

        root_lnk = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        user_lnk = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=1000)

        def _fake_readlink(p: str) -> str:
            if p == "/var":
                return "private/var"
            if p == "/tmp":
                return "private/tmp"
            if p == "/etc":
                return "private/etc"
            raise OSError("unexpected readlink")

        with (
            mock.patch.object(verify.sys, "platform", "darwin"),
            mock.patch.object(verify.os, "lstat", return_value=root_lnk),
            mock.patch.object(verify.os, "readlink", side_effect=_fake_readlink),
        ):
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/var/folders/xx/cert.json")),
                Path("/private/var/folders/xx/cert.json"),
            )
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/tmp/cert.json")),
                Path("/private/tmp/cert.json"),
            )
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/tmp"), allow_exact=True),
                Path("/private/tmp"),
            )
            self.assertEqual(verify._normalize_darwin_system_prefix(Path("/tmp")), Path("/tmp"))
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/tmp/../etc/passwd")),
                Path("/tmp/../etc/passwd"),
            )
            # Confirm _open_parent_dir_fd passes normalized private/var components to _step_dir_fd_chain
            captured: list[list[str]] = []

            def _fake_step(start_fd: int, comps: list[str], label: str, *, create_missing: bool = False) -> int:
                captured.append(list(comps))
                return start_fd

            with mock.patch.object(verify, "_step_dir_fd_chain", side_effect=_fake_step):
                dfd, leaf = verify._open_parent_dir_fd(Path("/var/folders/xx/cert.json"))
                os.close(dfd)
                self.assertEqual(leaf, "cert.json")
                self.assertEqual(captured[-1], ["private", "var", "folders", "xx"])
                # Nested planted symlink stays in traversed components for O_NOFOLLOW rejection
                dfd2, leaf2 = verify._open_parent_dir_fd(Path("/var/folders/xx/evil_link/cert.json"))
                os.close(dfd2)
                self.assertEqual(leaf2, "cert.json")
                self.assertEqual(captured[-1], ["private", "var", "folders", "xx", "evil_link"])

        # Non-root owner, hijacked target, and Linux must NOT normalize
        with (
            mock.patch.object(verify.sys, "platform", "darwin"),
            mock.patch.object(verify.os, "lstat", return_value=user_lnk),
            mock.patch.object(verify.os, "readlink", side_effect=_fake_readlink),
        ):
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/var/folders/xx/cert.json")),
                Path("/var/folders/xx/cert.json"),
            )
        with (
            mock.patch.object(verify.sys, "platform", "darwin"),
            mock.patch.object(verify.os, "lstat", return_value=root_lnk),
            mock.patch.object(verify.os, "readlink", return_value="private/evil"),
        ):
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/var/folders/xx/cert.json")),
                Path("/var/folders/xx/cert.json"),
            )
        with mock.patch.object(verify.sys, "platform", "linux"):
            self.assertEqual(
                verify._normalize_darwin_system_prefix(Path("/var/folders/xx/cert.json")),
                Path("/var/folders/xx/cert.json"),
            )


if __name__ == "__main__":
    unittest.main()
