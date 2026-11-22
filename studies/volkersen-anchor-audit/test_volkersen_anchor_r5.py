#!/usr/bin/env python3
"""
Assertion-free R5 unit & mutation-isolation test suite for `volkersen_anchor_audit.py`.
Zero bare `assert`s (100% `python3 -O` parity).
"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
import copy
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import volkersen_anchor_audit as mod


class _LyingSeq(Sequence):
    def __init__(self, items: list[int]) -> None:
        self._items = items

    def __len__(self) -> int:
        return 2

    def __getitem__(self, idx: int) -> int:
        return self._items[idx]

    def __iter__(self):
        return iter(self._items)


class TestVolkersenAnchorAudit(unittest.TestCase):
    def test_hardening_primitives_and_json(self) -> None:
        self.assertEqual(mod.require_int(2, "x", lo=2, hi=4), 2)
        self.assertEqual(mod.require_int(4, "x", lo=2, hi=4), 4)
        for bad in (True, False, 1.0, "2", None):
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.require_int(bad, "x")
            self.assertEqual(cm.exception.code, "INVALID_INT_TYPE")
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.require_int(1, "x", lo=2, hi=4)
        self.assertEqual(cm.exception.code, "INT_BELOW_MIN")
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.require_int(5, "x", lo=2, hi=4)
        self.assertEqual(cm.exception.code, "INT_ABOVE_MAX")

        self.assertEqual(mod.require_nonneg_rat(0, "r"), Fraction(0))
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.require_nonneg_rat(Fraction(-1, 2), "r")
        self.assertEqual(cm.exception.code, "NEGATIVE_RATIONAL")
        for bad_r in (True, 1.5, "1/2"):
            with self.assertRaises(mod.VolkersenAuditError):
                mod.require_rat(bad_r, "r")
        for nonpos in (0, -1, Fraction(0)):
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.require_pos_rat(nonpos, "p")
            self.assertEqual(cm.exception.code, "NON_POSITIVE_RATIONAL")

        self.assertEqual(mod.require_seq([1], "s", min_len=1, max_len=2), (1,))
        self.assertEqual(mod.require_seq((1, 2), "s", min_len=1, max_len=2), (1, 2))
        for bad_s in (_LyingSeq([1, 2, 3]), "ab", {1: 2}):
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.require_seq(bad_s, "s", min_len=1, max_len=3)
            self.assertEqual(cm.exception.code, "INVALID_SEQ_TYPE")
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.require_seq([], "s", min_len=1, max_len=2)
        self.assertEqual(cm.exception.code, "SEQ_TOO_SHORT")
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.require_seq([1, 2, 3], "s", min_len=1, max_len=2)
        self.assertEqual(cm.exception.code, "SEQ_TOO_LONG")
        self.assertEqual(mod.fmt_q(Fraction(6, 3)), "2")
        self.assertEqual(mod.fmt_q(Fraction(3, 4)), "3/4")

        # Iterative depth, digit, key, type & byte caps
        exact_d: object = 0
        for _ in range(mod.MAX_JSON_DEPTH):
            exact_d = [exact_d]
        mod.validate_json_value(exact_d)
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.validate_json_value([exact_d])
        self.assertEqual(cm.exception.code, "MAX_DEPTH_EXCEEDED")
        deep_py: object = 1
        for _ in range(1500):
            deep_py = [deep_py]
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.canonical_json_bytes(deep_py)
        self.assertEqual(cm.exception.code, "MAX_DEPTH_EXCEEDED")

        mod.validate_json_value({"a": mod.MAX_JSON_INT, "b": -mod.MAX_JSON_INT, "c": True, "d": None, "e": "ok"})
        for bad_obj, code in (
            ({1: "a"}, "NON_STR_KEY"),
            ({"x": 1.0}, "FLOAT_FORBIDDEN"),
            ({"x": (1, 2)}, "UNSUPPORTED_JSON_TYPE"),
            ({"x": mod.MAX_JSON_INT + 1}, "INT_TOO_LARGE"),
        ):
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.validate_json_value(bad_obj)
            self.assertEqual(cm.exception.code, code)

        ok_400 = '{"n":' + ("9" * mod.MAX_INT_DIGITS) + ',"m":-' + ("9" * mod.MAX_INT_DIGITS) + "}"
        self.assertEqual(mod.parse_strict_json(ok_400)["n"], 10**mod.MAX_INT_DIGITS - 1)
        for bad_txt, code in (
            ('{"n":' + ("9" * (mod.MAX_INT_DIGITS + 1)) + "}", "INT_DIGIT_LIMIT"),
            ('{"n":' + ("9" * 5000) + "}", "INT_DIGIT_LIMIT"),
            ('{"x":1.0}', "JSON_FLOAT_FORBIDDEN"),
            ('{"x":NaN}', "JSON_CONSTANT_FORBIDDEN"),
            ('{"a":1,"a":2}', "JSON_DUPLICATE_KEY"),
            ("", "EMPTY_JSON"),
            ("{bad", "JSON_PARSE_ERROR"),
        ):
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.parse_strict_json(bad_txt)
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.parse_strict_json(b"{}")  # type: ignore[arg-type]
        self.assertEqual(cm.exception.code, "INVALID_JSON_INPUT")
        exact_cap = '{"s":"' + ("a" * (mod.MAX_JSON_BYTES - 8)) + '"}'
        self.assertIn("s", mod.parse_strict_json(exact_cap))
        over_cap = '{"s":"' + ("a" * (mod.MAX_JSON_BYTES - 7)) + '"}'
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.parse_strict_json(over_cap)
        self.assertEqual(cm.exception.code, "JSON_TOO_LARGE")

    def test_volkersen_physics_receiving_gates_and_boundaries(self) -> None:
        payload = mod.build_study_payload()
        self.assertTrue(payload["all_passed"])
        ctrl = payload["receiving_gate_controls"]
        self.assertFalse(ctrl["rigid_rc_uniform_alpha_1_4_fails"]["receiving_gate_pass"])
        self.assertTrue(ctrl["rigid_rc_front_dense_graded_alpha_1_4_passes"]["receiving_gate_pass"])
        self.assertFalse(ctrl["sym_lap_uniform_alpha_3_4_fails"]["receiving_gate_pass"])
        self.assertTrue(ctrl["sym_lap_end_dense_graded_alpha_3_4_passes"]["receiving_gate_pass"])
        self.assertFalse(ctrl["over_perforated_10rows_ligament_fails"]["receiving_gate_pass"])

        # m = 2 boundary
        sol2 = mod.solve_volkersen_rows_q([Fraction(220)], Fraction(2), Fraction(1))
        self.assertEqual(sol2["row_forces"], [Fraction(1, 2), Fraction(1, 2)])
        self.assertTrue(sol2["tridiagonal_residual_zero"])

        # Ligament floor boundary (>= 1 rejected)
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.evaluate_anchor_receiving_gate_q(
                [Fraction(220)] * 5,
                anchors_per_row=2,
                hole_diam_mm=12,
                alpha_total=1,
                substrate_ratio=0,
                total_sustained_kN=100,
                per_anchor_creep_cap_kN=20,
                min_net_ligament_fraction=1,
            )
        self.assertEqual(cm.exception.code, "INVALID_LIGAMENT_FLOOR")

        # Perforation optimum boundaries: ratio <= 2 fails, 5/2 passes; m_max == 24 passes, 25 fails
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.evaluate_perforation_optimum_q(Fraction(2), Fraction(1), Fraction(2), Fraction(0), Fraction(10), Fraction(50))
        self.assertEqual(cm.exception.code, "ZONE_TOO_SHORT")
        self.assertEqual(
            mod.evaluate_perforation_optimum_q(Fraction(5, 2), Fraction(1), Fraction(2), Fraction(0), Fraction(10), Fraction(50))["m_max"],
            2,
        )
        self.assertEqual(
            mod.evaluate_perforation_optimum_q(Fraction(25), Fraction(1), Fraction(2), Fraction(0), Fraction(10), Fraction(50))["m_max"],
            24,
        )
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.evaluate_perforation_optimum_q(Fraction(26), Fraction(1), Fraction(2), Fraction(0), Fraction(10), Fraction(50))
        self.assertEqual(cm.exception.code, "SWEEP_TOO_LARGE")

    def test_certificate_verification_file_io_and_cli(self) -> None:
        cert = mod.build_certificate()
        ok = mod.verify_certificate_dict(cert)
        self.assertTrue(ok["valid"])

        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.verify_certificate_dict([])
        self.assertEqual(cm.exception.code, "CERT_NOT_DICT")

        missing = copy.deepcopy(cert)
        del missing["payload"]
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.verify_certificate_dict(missing)
        self.assertEqual(cm.exception.code, "CERT_SCHEMA_KEYS")

        for key, bad_val, code in (
            ("schema", "wrong", "CERT_SCHEMA_MISMATCH"),
            ("upstream_repo", "wrong", "CERT_REPO_MISMATCH"),
            ("upstream_main_sha", "0" * 40, "CERT_SHA_MISMATCH"),
        ):
            mut = copy.deepcopy(cert)
            mut[key] = bad_val
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.verify_certificate_dict(mut)
            self.assertEqual(cm.exception.code, code)

        for field in ("payload_sha256", "certificate_sha256"):
            for bad_val, code in (
                (123, "INVALID_DIGEST_TYPE"),
                ("a" * 63, "INVALID_DIGEST_LEN"),
                ("G" + "a" * 63, "INVALID_DIGEST_HEX"),
            ):
                mut = copy.deepcopy(cert)
                mut[field] = bad_val
                with self.assertRaises(mod.VolkersenAuditError) as cm:
                    mod.verify_certificate_dict(mut)
                self.assertEqual(cm.exception.code, code)

        bad_p = copy.deepcopy(cert)
        bad_p["payload_sha256"] = ("1" if cert["payload_sha256"][0] == "0" else "0") + cert["payload_sha256"][1:]
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.verify_certificate_dict(bad_p)
        self.assertEqual(cm.exception.code, "PAYLOAD_SHA_MISMATCH")

        bad_c = copy.deepcopy(cert)
        bad_c["certificate_sha256"] = ("1" if cert["certificate_sha256"][0] == "0" else "0") + cert["certificate_sha256"][1:]
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.verify_certificate_dict(bad_c)
        self.assertEqual(cm.exception.code, "CERTIFICATE_SHA_MISMATCH")

        # Re-hashed bool-as-int forgery rejected
        forged = copy.deepcopy(cert)
        forged["payload"]["upstream_anchors"]["anchors_per_row"] = True
        fpsha = hashlib.sha256(mod.canonical_json_bytes(forged["payload"])).hexdigest()
        forged["payload_sha256"] = fpsha
        env = {
            "schema": forged["schema"],
            "upstream_repo": forged["upstream_repo"],
            "upstream_main_sha": forged["upstream_main_sha"],
            "payload_sha256": fpsha,
            "payload": forged["payload"],
        }
        forged["certificate_sha256"] = hashlib.sha256(mod.canonical_json_bytes(env)).hexdigest()
        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.verify_certificate_dict(forged)
        self.assertEqual(cm.exception.code, "RECOMPUTED_PAYLOAD_DRIFT")

        with self.assertRaises(mod.VolkersenAuditError) as cm:
            mod.check_certificate_file("bad_type.json")  # type: ignore[arg-type]
        self.assertEqual(cm.exception.code, "INVALID_PATH_TYPE")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w_path = root / "cert.json"
            w_out = io.StringIO()
            with redirect_stdout(w_out):
                self.assertEqual(mod.main(["--write", str(w_path)]), 0)
            self.assertIn("WROTE", w_out.getvalue())

            raw_valid = w_path.read_bytes()
            out_buf = io.BytesIO()
            fake_out = io.TextIOWrapper(out_buf, encoding="utf-8")
            with redirect_stdout(fake_out):
                self.assertEqual(mod.main(["--standalone", "--json"]), 0)
            fake_out.flush()
            self.assertEqual(out_buf.getvalue(), raw_valid)
            self.assertEqual(mod.DEFAULT_CERT_PATH.read_bytes(), raw_valid)

            s_out = io.StringIO()
            with redirect_stdout(s_out):
                self.assertEqual(mod.main([]), 0)
            self.assertIn("OK", s_out.getvalue())

            c_out = io.StringIO()
            with redirect_stdout(c_out):
                self.assertEqual(mod.main(["--check", str(w_path)]), 0)
            self.assertIn("OK", c_out.getvalue())

            link_f = root / "link.json"
            link_f.symlink_to(w_path)
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(link_f)
            self.assertEqual(cm.exception.code, "SYMLINK_FORBIDDEN")

            rdir = root / "rdir"
            rdir.mkdir()
            ldir = root / "ldir"
            ldir.symlink_to(rdir)
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(ldir / "c.json")
            self.assertEqual(cm.exception.code, "SYMLINK_ANCESTOR_FORBIDDEN")

            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(root / "missing.json")
            self.assertEqual(cm.exception.code, "CERT_FILE_NOT_FOUND")

            cap_f = root / "cap.json"
            cap_f.write_bytes(b" " * mod.MAX_JSON_BYTES)
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(cap_f)
            self.assertEqual(cm.exception.code, "EMPTY_JSON")

            big_f = root / "big.json"
            big_f.write_bytes(b" " * (mod.MAX_JSON_BYTES + 1))
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(big_f)
            self.assertEqual(cm.exception.code, "CERT_FILE_TOO_LARGE")

            crlf_f = root / "crlf.json"
            crlf_f.write_bytes(raw_valid.rstrip(b"\n") + b"\r\n")
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(crlf_f)
            self.assertEqual(cm.exception.code, "CRLF_FORBIDDEN")

            bad_u8 = root / "u8.json"
            bad_u8.write_bytes(b"\xff\xfe")
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(bad_u8)
            self.assertEqual(cm.exception.code, "INVALID_UTF8")

            non_canon = root / "nc.json"
            non_canon.write_bytes(raw_valid + b"\n")
            with self.assertRaises(mod.VolkersenAuditError) as cm:
                mod.check_certificate_file(non_canon)
            self.assertEqual(cm.exception.code, "NON_CANONICAL_CERT_BYTES")

            huge_int = root / "huge.json"
            huge_int.write_text('{"n":' + ("9" * 5000) + "}\n", encoding="utf-8")
            deep_f = root / "deep.json"
            deep_f.write_text("[" * 1500 + "0" + "]" * 1500 + "\n", encoding="utf-8")
            for p_bad in (huge_int, deep_f, root / "absent.json"):
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertEqual(mod.main(["--check", str(p_bad)]), 2)
                self.assertIn("REJECT", err.getvalue())


if __name__ == "__main__":
    unittest.main()
