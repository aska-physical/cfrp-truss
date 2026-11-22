#!/usr/bin/env python3
"""
Receiving-Side Exact-Q Discrete Volkersen Anchor Shear-Lag & Perforation-Tear Checker
for `aska-physical/cfrp-truss` (`origin/main @ 3c11631e223b7122`, `verify.py:278,282-283,369-371`).

Evidence classification (`aska-physical/dock#8`): MODEL / CHECKER (exact pure-Q algebra,
not bench measurement). Zero floats on trust paths. Zero bare `assert`s (`-O` safe).
Supports:
  - `--standalone --json`: emit canonical zero-float JSON certificate to stdout
  - `--write [PATH]`: write canonical certificate and immediately re-verify it
  - `--check PATH`: fail-closed verification (symlink, size, CRLF, UTF-8, iterative depth/digit
    JSON validation, `hmac.compare_digest` on `payload_sha256` & `certificate_sha256`, exact
    recomputation, canonical bytes). Exits with code `2` cleanly on any error.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any

CERT_SCHEMA = "aska-physical.cfrp-truss.volkersen-anchor-audit.v1"
UPSTREAM_REPO = "aska-physical/cfrp-truss"
UPSTREAM_MAIN_SHA = "3c11631e223b71227531d10286abd71bc04d67b7"
HERE = Path(__file__).resolve().parent
DEFAULT_CERT_PATH = HERE / "volkersen_anchor_certificate.json"

MAX_JSON_DEPTH = 32
MAX_JSON_BYTES = 2_000_000
MAX_INT_DIGITS = 400
MAX_JSON_INT = 10**MAX_INT_DIGITS
MAX_SEQ_LEN = 128
_HEX_LOWER = frozenset("0123456789abcdef")
_CERT_KEYS = frozenset({"schema", "upstream_repo", "upstream_main_sha", "payload_sha256", "certificate_sha256", "payload"})


class VolkersenAuditError(ValueError):
    """Fail-closed typed error (never stripped under python3 -O)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# Strict Input Hardening & Iterative Zero-Float Canonical JSON
# ---------------------------------------------------------------------------

def require_int(val: Any, label: str = "int", *, lo: int | None = None, hi: int | None = None) -> int:
    if type(val) is not int:
        raise VolkersenAuditError("INVALID_INT_TYPE", f"{label} must be strict int")
    if lo is not None and val < lo:
        raise VolkersenAuditError("INT_BELOW_MIN", f"{label} below min {lo}")
    if hi is not None and val > hi:
        raise VolkersenAuditError("INT_ABOVE_MAX", f"{label} above max {hi}")
    return val


def require_rat(val: Any, label: str = "rational") -> Fraction:
    if type(val) is Fraction:
        return val
    if type(val) is int:
        return Fraction(val, 1)
    raise VolkersenAuditError("INVALID_RATIONAL_TYPE", f"{label} must be strict int or Fraction")


def require_nonneg_rat(val: Any, label: str = "nonneg_rational") -> Fraction:
    q = require_rat(val, label)
    if q < 0:
        raise VolkersenAuditError("NEGATIVE_RATIONAL", f"{label} must be >= 0")
    return q


def require_pos_rat(val: Any, label: str = "pos_rational") -> Fraction:
    q = require_rat(val, label)
    if q <= 0:
        raise VolkersenAuditError("NON_POSITIVE_RATIONAL", f"{label} must be > 0")
    return q


def require_seq(val: Any, label: str = "seq", *, min_len: int = 1, max_len: int = MAX_SEQ_LEN) -> tuple[Any, ...]:
    if type(val) not in (list, tuple):
        raise VolkersenAuditError("INVALID_SEQ_TYPE", f"{label} must be strict list or tuple")
    n = len(val)
    if n < min_len:
        raise VolkersenAuditError("SEQ_TOO_SHORT", f"{label} length {n} < {min_len}")
    if n > max_len:
        raise VolkersenAuditError("SEQ_TOO_LONG", f"{label} length {n} > {max_len}")
    return tuple(val)


def fmt_q(q: Fraction) -> str:
    r = require_rat(q, "q")
    if r.denominator == 1:
        return str(r.numerator)
    return f"{r.numerator}/{r.denominator}"


def validate_json_value(obj: Any, *, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Iterative zero-float, depth-capped, strict-key JSON tree validator (zero recursion)."""
    limit = require_int(max_depth, "max_depth", lo=1, hi=128)
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        cur, depth = stack.pop()
        if depth > limit:
            raise VolkersenAuditError("MAX_DEPTH_EXCEEDED", f"JSON nesting exceeds {limit}")
        t = type(cur)
        if t is dict:
            for k, v in cur.items():
                if type(k) is not str:
                    raise VolkersenAuditError("NON_STR_KEY", "dict keys must be strict str")
                stack.append((v, depth + 1))
        elif t is list:
            for item in cur:
                stack.append((item, depth + 1))
        elif t is int:
            if abs(cur) > MAX_JSON_INT:
                raise VolkersenAuditError("INT_TOO_LARGE", "integer exceeds digit cap")
        elif t in (str, bool) or cur is None:
            continue
        elif t is float:
            raise VolkersenAuditError("FLOAT_FORBIDDEN", "float values are forbidden")
        else:
            raise VolkersenAuditError("UNSUPPORTED_JSON_TYPE", f"unsupported type {t.__name__}")


def _reject_float_token(tok: str) -> Any:
    raise VolkersenAuditError("JSON_FLOAT_FORBIDDEN", f"float literal forbidden: {tok!r}")


def _reject_constant_token(tok: str) -> Any:
    raise VolkersenAuditError("JSON_CONSTANT_FORBIDDEN", f"non-RFC8259 constant forbidden: {tok!r}")


def _parse_capped_int_token(tok: str) -> int:
    digits = tok[1:] if tok.startswith("-") else tok
    if len(digits) > MAX_INT_DIGITS:
        raise VolkersenAuditError("INT_DIGIT_LIMIT", f"integer token exceeds {MAX_INT_DIGITS} digits")
    return int(tok)


def _pairs_no_dup(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise VolkersenAuditError("JSON_DUPLICATE_KEY", f"duplicate JSON key: {k!r}")
        out[k] = v
    return out


def parse_strict_json(text: str) -> Any:
    if type(text) is not str:
        raise VolkersenAuditError("INVALID_JSON_INPUT", "JSON input must be strict str")
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise VolkersenAuditError("JSON_TOO_LARGE", "JSON input exceeds byte cap")
    if not text.strip():
        raise VolkersenAuditError("EMPTY_JSON", "JSON input must be non-empty")
    try:
        parsed = json.loads(
            text,
            parse_int=_parse_capped_int_token,
            parse_float=_reject_float_token,
            parse_constant=_reject_constant_token,
            object_pairs_hook=_pairs_no_dup,
        )
    except VolkersenAuditError:
        raise
    except (ValueError, RecursionError) as exc:
        raise VolkersenAuditError("JSON_PARSE_ERROR", str(exc)) from exc
    validate_json_value(parsed)
    return parsed


def canonical_json_bytes(obj: Any) -> bytes:
    validate_json_value(obj)
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Exact-Q Generalized Discrete Volkersen Shear-Lag & Receiving-Side Gate
# ---------------------------------------------------------------------------

def solve_volkersen_rows_q(
    bay_lengths: Any,
    alpha_total: Any,
    substrate_ratio: Any = Fraction(0),
    total_load: Any = Fraction(1),
) -> dict[str, Any]:
    """
    Exact-Q SPD tridiagonal Volkersen shear-lag solver for m = len(bay_lengths) + 1 longitudinal
    dowel rows separated by bays Delta_1..Delta_{m-1} (sum = L_zone):
        -T_{i-1} + (2 + (1 + r) * lambda_i) T_i - T_{i+1} = lambda_i * W,  T_0 = 0, T_m = W,
    where lambda_i = alpha_total * (Delta_i / L_zone), r = (EA_cfrp / EA_substrate) >= 0:
      - r = 0: rigid concrete substrate (cfrp-truss CFRP-to-RC end anchorage; peak at lead row 1)
      - r = 1: symmetric equal-stiffness double-sheet lap (equal peaks at rows 1 and m).
    """
    bays_raw = require_seq(bay_lengths, "bay_lengths", min_len=1, max_len=64)
    bays = [require_pos_rat(d, f"bay_lengths[{i}]") for i, d in enumerate(bays_raw)]
    alpha = require_pos_rat(alpha_total, "alpha_total")
    r_ratio = require_nonneg_rat(substrate_ratio, "substrate_ratio")
    w = require_pos_rat(total_load, "total_load")

    l_zone = sum(bays, Fraction(0))
    lams = [alpha * d / l_zone for d in bays]
    n = len(bays)
    c_p = [Fraction(0)] * n
    d_p = [Fraction(0)] * n
    one_plus_r = Fraction(1) + r_ratio

    for i in range(n):
        diag = Fraction(2) + one_plus_r * lams[i]
        rhs_i = lams[i] * w + (w if i == n - 1 else Fraction(0))
        if i == 0:
            c_p[0] = (Fraction(-1) / diag) if n > 1 else Fraction(0)
            d_p[0] = rhs_i / diag
        else:
            denom = diag + c_p[i - 1]
            c_p[i] = (Fraction(-1) / denom) if i < n - 1 else Fraction(0)
            d_p[i] = (rhs_i + d_p[i - 1]) / denom

    T = [Fraction(0)] * n
    T[n - 1] = d_p[n - 1]
    for i in range(n - 2, -1, -1):
        T[i] = d_p[i] - c_p[i] * T[i + 1]

    Ts = [Fraction(0)] + T + [w]
    residual_ok = all(
        (-Ts[i] + (Fraction(2) + one_plus_r * lams[i]) * Ts[i + 1] - Ts[i + 2]) == lams[i] * w
        for i in range(n)
    )
    forces = [Ts[i + 1] - Ts[i] for i in range(n + 1)]
    m = n + 1
    f_max = max(forces)
    eta_peak = Fraction(m) * f_max / w
    return {
        "n_long_rows": m,
        "l_zone": l_zone,
        "row_forces": forces,
        "max_row_force": f_max,
        "peaking_factor_eta": eta_peak,
        "force_sum_exact": sum(forces, Fraction(0)) == w,
        "tridiagonal_residual_zero": residual_ok,
    }


def evaluate_anchor_receiving_gate_q(
    bay_lengths_mm: Any,
    *,
    anchors_per_row: int,
    hole_diam_mm: Any,
    alpha_total: Any,
    substrate_ratio: Any,
    total_sustained_kN: Any,
    per_anchor_creep_cap_kN: Any,
    min_net_ligament_fraction: Any,
) -> dict[str, Any]:
    """
    Receiving-side pass/fail gate (`aska-physical/dock#8`): checks BOTH Volkersen peak-row
    sustained creep load (replacing uniform `T_sus / n_anchors` at `verify.py:278`) AND
    longitudinal net-section ligament ratio `1 - m * d_hole / L_zone >= min_net_ligament_fraction`.
    """
    apr = require_int(anchors_per_row, "anchors_per_row", lo=1, hi=8)
    dh = require_pos_rat(hole_diam_mm, "hole_diam_mm")
    t_sus = require_pos_rat(total_sustained_kN, "total_sustained_kN")
    cap_1 = require_pos_rat(per_anchor_creep_cap_kN, "per_anchor_creep_cap_kN")
    min_lig = require_pos_rat(min_net_ligament_fraction, "min_net_ligament_fraction")
    if min_lig >= 1:
        raise VolkersenAuditError("INVALID_LIGAMENT_FLOOR", "min_net_ligament_fraction must be < 1")

    sol = solve_volkersen_rows_q(bay_lengths_mm, alpha_total, substrate_ratio, t_sus)
    m = sol["n_long_rows"]
    l_zone = sol["l_zone"]
    n_anchors = m * apr
    uniform_per_anchor = t_sus / Fraction(n_anchors)
    peak_per_anchor = sol["max_row_force"] / Fraction(apr)
    ligament_frac = Fraction(1) - Fraction(m) * dh / l_zone

    creep_pass = peak_per_anchor <= cap_1
    ligament_pass = ligament_frac >= min_lig
    gate_pass = all((sol["tridiagonal_residual_zero"], sol["force_sum_exact"], creep_pass, ligament_pass))
    return {
        "n_long_rows": m,
        "n_anchors": n_anchors,
        "l_zone_mm": fmt_q(l_zone),
        "uniform_per_anchor_kN": fmt_q(uniform_per_anchor),
        "peak_per_anchor_kN": fmt_q(peak_per_anchor),
        "per_anchor_creep_cap_kN": fmt_q(cap_1),
        "peaking_factor_eta": fmt_q(sol["peaking_factor_eta"]),
        "net_ligament_fraction": fmt_q(ligament_frac),
        "uniform_assumption_passes": uniform_per_anchor <= cap_1,
        "volkersen_creep_gate_passes": creep_pass,
        "ligament_tear_gate_passes": ligament_pass,
        "receiving_gate_pass": gate_pass,
    }


def evaluate_perforation_optimum_q(
    l_zone_mm: Any,
    hole_diam_mm: Any,
    alpha_total: Any,
    substrate_ratio: Any,
    row_pull_cap_kN: Any,
    intact_seam_tear_kN: Any,
) -> dict[str, Any]:
    """
    Exact-Q interior optimum m* balancing Volkersen lead-row pull-out capacity against
    perforation net-section ligament tear capacity `T_0 * (1 - m * d_hole / L_zone)`.
    """
    l_zone = require_pos_rat(l_zone_mm, "l_zone_mm")
    dh = require_pos_rat(hole_diam_mm, "hole_diam_mm")
    alpha = require_pos_rat(alpha_total, "alpha_total")
    r_ratio = require_nonneg_rat(substrate_ratio, "substrate_ratio")
    f_row_cap = require_pos_rat(row_pull_cap_kN, "row_pull_cap_kN")
    t0 = require_pos_rat(intact_seam_tear_kN, "intact_seam_tear_kN")

    max_ratio = l_zone / dh
    if max_ratio <= 2:
        raise VolkersenAuditError("ZONE_TOO_SHORT", "need l_zone_mm / hole_diam_mm > 2")
    m_max = (max_ratio.numerator - 1) // max_ratio.denominator
    if m_max > 24:
        raise VolkersenAuditError("SWEEP_TOO_LARGE", "interior optimum sweep capped at m_max <= 24")

    rows: list[dict[str, Any]] = []
    caps: list[Fraction] = []
    res_checks: list[bool] = []
    for m in range(1, m_max + 1):
        if m == 1:
            fmax = Fraction(1)
        else:
            sol = solve_volkersen_rows_q([l_zone / Fraction(m - 1)] * (m - 1), alpha, r_ratio)
            fmax = sol["max_row_force"]
            res_checks.append(sol["tridiagonal_residual_zero"])
            res_checks.append(sol["force_sum_exact"])
        w_pull = f_row_cap / fmax
        w_tear = t0 * (Fraction(1) - Fraction(m) * dh / l_zone)
        w_cap = w_pull if w_pull <= w_tear else w_tear
        caps.append(w_cap)
        rows.append({
            "m_rows": m,
            "lead_row_fraction": fmt_q(fmax),
            "w_pull_kN": fmt_q(w_pull),
            "w_tear_kN": fmt_q(w_tear),
            "w_cap_kN": fmt_q(w_cap),
        })

    best_cap = max(caps)
    m_star = caps.index(best_cap) + 1
    return {
        "m_max": m_max,
        "m_star": m_star,
        "w_cap_star_kN": fmt_q(best_cap),
        "strict_interior_optimum": 1 < m_star < m_max,
        "all_tridiagonal_residuals_zero": all(res_checks),
        "sweep": rows,
    }


def build_study_payload() -> dict[str, Any]:
    """Build deterministic exact-Q Rank #1 receiving-side audit payload for aska-physical/cfrp-truss."""
    # Exact upstream cfrp-truss @ 3c11631e end-anchorage parameters (verify.py:278,369-371, cert:82-108):
    # 12 anchors = 6 longitudinal rows x 2 across width; s_long = 220 mm -> L_zone = 1,100 mm;
    # uniform sustained per anchor = 12.66 kN -> T_sus_group = 12 * 12.66 = 151.92 kN;
    # ACI 318-19 §17.5.2.2 sustained creep cap = 16.14 kN per anchor; hole_diam = 12 mm.
    unif_bays = [Fraction(220)] * 5
    front_dense_bays = [Fraction(100), Fraction(150), Fraction(200), Fraction(250), Fraction(400)]
    sym_end_dense_bays = [Fraction(110), Fraction(165), Fraction(550), Fraction(165), Fraction(110)]
    t_sus_kN = Fraction(15192, 100)
    cap_sus_kN = Fraction(1614, 100)
    d_hole_mm = Fraction(12)
    lig_floor = Fraction(9, 10)  # require >= 90% net longitudinal ligament (6*12/1100 = 6.55% loss -> 93.45% >= 90%)

    # 1. Receiving-side PASS/FAIL separation (rigid-RC r=0, alpha=1/4): uniform FAILS, front-dense graded PASSES!
    rc_unif_fail = evaluate_anchor_receiving_gate_q(
        unif_bays,
        anchors_per_row=2,
        hole_diam_mm=d_hole_mm,
        alpha_total=Fraction(1, 4),
        substrate_ratio=Fraction(0),
        total_sustained_kN=t_sus_kN,
        per_anchor_creep_cap_kN=cap_sus_kN,
        min_net_ligament_fraction=lig_floor,
    )
    rc_graded_pass = evaluate_anchor_receiving_gate_q(
        front_dense_bays,
        anchors_per_row=2,
        hole_diam_mm=d_hole_mm,
        alpha_total=Fraction(1, 4),
        substrate_ratio=Fraction(0),
        total_sustained_kN=t_sus_kN,
        per_anchor_creep_cap_kN=cap_sus_kN,
        min_net_ligament_fraction=lig_floor,
    )

    # 2. Symmetric double-sheet lap (r=1, alpha=3/4): uniform FAILS, symmetric end-dense graded PASSES!
    sym_unif_fail = evaluate_anchor_receiving_gate_q(
        unif_bays,
        anchors_per_row=2,
        hole_diam_mm=d_hole_mm,
        alpha_total=Fraction(3, 4),
        substrate_ratio=Fraction(1),
        total_sustained_kN=t_sus_kN,
        per_anchor_creep_cap_kN=cap_sus_kN,
        min_net_ligament_fraction=lig_floor,
    )
    sym_graded_pass = evaluate_anchor_receiving_gate_q(
        sym_end_dense_bays,
        anchors_per_row=2,
        hole_diam_mm=d_hole_mm,
        alpha_total=Fraction(3, 4),
        substrate_ratio=Fraction(1),
        total_sustained_kN=t_sus_kN,
        per_anchor_creep_cap_kN=cap_sus_kN,
        min_net_ligament_fraction=lig_floor,
    )

    # 3. High shear-lag peaking benchmark (m=6, alpha=6): exact eta_peak > 2.28x (sym) & > 3.89x (rigid-RC)
    stiff_sym_unif = solve_volkersen_rows_q(unif_bays, Fraction(6), Fraction(1))
    stiff_sym_grad = solve_volkersen_rows_q(
        [Fraction(1100, 12), Fraction(1100, 6), Fraction(550), Fraction(1100, 6), Fraction(1100, 12)],
        Fraction(6),
        Fraction(1),
    )
    stiff_rc_unif = solve_volkersen_rows_q(unif_bays, Fraction(6), Fraction(0))
    stiff_rc_grad = solve_volkersen_rows_q(front_dense_bays, Fraction(6), Fraction(0))

    # 4. Excessive perforation must-fail control (over-drilled 10 rows with 12 mm holes over 1,100 mm -> 89.09% < 90%!)
    over_perforated_fail = evaluate_anchor_receiving_gate_q(
        [Fraction(1100, 9)] * 9,
        anchors_per_row=2,
        hole_diam_mm=d_hole_mm,
        alpha_total=Fraction(1, 4),
        substrate_ratio=Fraction(0),
        total_sustained_kN=t_sus_kN,
        per_anchor_creep_cap_kN=cap_sus_kN,
        min_net_ligament_fraction=lig_floor,
    )

    # 5. Exact interior perforation-tear optimum m* over L_zone = 1,100 mm, effective bond-cone spacing 110 mm (m_max = 9)
    # (110 mm = ~cNa width/pitch overlap scale from cfrp-truss cert:86 `cNa_mm = 116.52 mm`)
    interior_opt_rc = evaluate_perforation_optimum_q(
        Fraction(1100), Fraction(110), Fraction(4), Fraction(0), Fraction(3228, 100), Fraction(150)
    )
    interior_opt_sym = evaluate_perforation_optimum_q(
        Fraction(1100), Fraction(110), Fraction(4), Fraction(1), Fraction(3228, 100), Fraction(150)
    )

    controls_ok = all((
        rc_unif_fail["uniform_assumption_passes"],
        not rc_unif_fail["volkersen_creep_gate_passes"],
        not rc_unif_fail["receiving_gate_pass"],
        rc_graded_pass["receiving_gate_pass"],
        sym_unif_fail["uniform_assumption_passes"],
        not sym_unif_fail["volkersen_creep_gate_passes"],
        not sym_unif_fail["receiving_gate_pass"],
        sym_graded_pass["receiving_gate_pass"],
        not over_perforated_fail["ligament_tear_gate_passes"],
        not over_perforated_fail["receiving_gate_pass"],
        stiff_sym_unif["peaking_factor_eta"] == Fraction(349, 153),
        stiff_sym_grad["max_row_force"] == Fraction(7, 22),
        stiff_sym_grad["max_row_force"] < stiff_sym_unif["max_row_force"],
        stiff_rc_unif["peaking_factor_eta"] > Fraction(389, 100),
        stiff_rc_grad["max_row_force"] < stiff_rc_unif["max_row_force"],
        interior_opt_rc["all_tridiagonal_residuals_zero"],
        interior_opt_rc["strict_interior_optimum"],
        interior_opt_sym["all_tridiagonal_residuals_zero"],
        interior_opt_sym["strict_interior_optimum"],
    ))

    return {
        "evidence_label": "model_checker_exact_Q_not_bench_measurement",
        "upstream_anchors": {
            "repo": UPSTREAM_REPO,
            "main_sha": UPSTREAM_MAIN_SHA,
            "target_lines": "verify.py:278,282-283,369-371; results/certificate.json:82-108",
            "n_anchors": 12,
            "n_long_rows": 6,
            "anchors_per_row": 2,
            "l_zone_mm": "1100",
            "hole_diam_mm": "12",
            "total_sustained_kN": fmt_q(t_sus_kN),
            "per_anchor_creep_cap_kN": fmt_q(cap_sus_kN),
        },
        "receiving_gate_controls": {
            "rigid_rc_uniform_alpha_1_4_fails": rc_unif_fail,
            "rigid_rc_front_dense_graded_alpha_1_4_passes": rc_graded_pass,
            "sym_lap_uniform_alpha_3_4_fails": sym_unif_fail,
            "sym_lap_end_dense_graded_alpha_3_4_passes": sym_graded_pass,
            "over_perforated_10rows_ligament_fails": over_perforated_fail,
        },
        "high_shear_lag_alpha6_benchmark": {
            "sym_uniform_eta_peak": fmt_q(stiff_sym_unif["peaking_factor_eta"]),
            "sym_graded_eta_peak": fmt_q(stiff_sym_grad["peaking_factor_eta"]),
            "sym_graded_row_forces": [fmt_q(x) for x in stiff_sym_grad["row_forces"]],
            "rigid_rc_uniform_eta_peak": fmt_q(stiff_rc_unif["peaking_factor_eta"]),
            "rigid_rc_graded_eta_peak": fmt_q(stiff_rc_grad["peaking_factor_eta"]),
            "rigid_rc_peak_reduction_fraction": fmt_q(
                (stiff_rc_unif["max_row_force"] - stiff_rc_grad["max_row_force"]) / stiff_rc_unif["max_row_force"]
            ),
        },
        "perforation_tear_interior_optima": {
            "rigid_rc_r0": interior_opt_rc,
            "sym_lap_r1": interior_opt_sym,
        },
        "all_passed": controls_ok,
    }


# ---------------------------------------------------------------------------
# Fail-Closed Certificate Builder, Checker & CLI
# ---------------------------------------------------------------------------

def _require_sha256_hex(val: Any, label: str) -> str:
    if type(val) is not str:
        raise VolkersenAuditError("INVALID_DIGEST_TYPE", f"{label} must be strict str")
    if len(val) != 64:
        raise VolkersenAuditError("INVALID_DIGEST_LEN", f"{label} must have length 64")
    if any(ch not in _HEX_LOWER for ch in val):
        raise VolkersenAuditError("INVALID_DIGEST_HEX", f"{label} must be lowercase hex")
    return val


def _require_no_symlink_path(path: Path) -> None:
    if path.is_symlink():
        raise VolkersenAuditError("SYMLINK_FORBIDDEN", f"symlink path forbidden: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise VolkersenAuditError("SYMLINK_ANCESTOR_FORBIDDEN", f"symlink ancestor forbidden: {parent}")


def build_certificate() -> dict[str, Any]:
    payload = build_study_payload()
    payload_sha256 = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    envelope = {
        "schema": CERT_SCHEMA,
        "upstream_repo": UPSTREAM_REPO,
        "upstream_main_sha": UPSTREAM_MAIN_SHA,
        "payload_sha256": payload_sha256,
        "payload": payload,
    }
    certificate_sha256 = hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    return {
        "schema": CERT_SCHEMA,
        "upstream_repo": UPSTREAM_REPO,
        "upstream_main_sha": UPSTREAM_MAIN_SHA,
        "payload_sha256": payload_sha256,
        "certificate_sha256": certificate_sha256,
        "payload": payload,
    }


def verify_certificate_dict(cert: Any) -> dict[str, Any]:
    if type(cert) is not dict:
        raise VolkersenAuditError("CERT_NOT_DICT", "certificate root must be a JSON object")
    if set(cert.keys()) != _CERT_KEYS:
        raise VolkersenAuditError("CERT_SCHEMA_KEYS", f"unexpected keys: {sorted(cert.keys())}")
    if cert["schema"] != CERT_SCHEMA:
        raise VolkersenAuditError("CERT_SCHEMA_MISMATCH", "schema mismatch")
    if cert["upstream_repo"] != UPSTREAM_REPO:
        raise VolkersenAuditError("CERT_REPO_MISMATCH", "upstream_repo mismatch")
    if cert["upstream_main_sha"] != UPSTREAM_MAIN_SHA:
        raise VolkersenAuditError("CERT_SHA_MISMATCH", "upstream_main_sha mismatch")

    stored_payload_sha = _require_sha256_hex(cert["payload_sha256"], "payload_sha256")
    stored_cert_sha = _require_sha256_hex(cert["certificate_sha256"], "certificate_sha256")
    computed_payload_sha = hashlib.sha256(canonical_json_bytes(cert["payload"])).hexdigest()
    if not hmac.compare_digest(stored_payload_sha, computed_payload_sha):
        raise VolkersenAuditError("PAYLOAD_SHA_MISMATCH", "payload_sha256 mismatch")

    envelope = {
        "schema": cert["schema"],
        "upstream_repo": cert["upstream_repo"],
        "upstream_main_sha": cert["upstream_main_sha"],
        "payload_sha256": stored_payload_sha,
        "payload": cert["payload"],
    }
    computed_cert_sha = hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    if not hmac.compare_digest(stored_cert_sha, computed_cert_sha):
        raise VolkersenAuditError("CERTIFICATE_SHA_MISMATCH", "certificate_sha256 mismatch")

    expected = build_certificate()
    if not hmac.compare_digest(computed_payload_sha, expected["payload_sha256"]):
        raise VolkersenAuditError("RECOMPUTED_PAYLOAD_DRIFT", "payload digest differs from exact recomputation")

    return {
        "valid": True,
        "schema": CERT_SCHEMA,
        "payload_sha256": computed_payload_sha,
        "certificate_sha256": computed_cert_sha,
    }


def check_certificate_file(path: Path) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise VolkersenAuditError("INVALID_PATH_TYPE", "path must be a pathlib.Path")
    _require_no_symlink_path(path)
    if not path.is_file():
        raise VolkersenAuditError("CERT_FILE_NOT_FOUND", f"certificate file not found: {path}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise VolkersenAuditError("CERT_FILE_TOO_LARGE", "certificate file exceeds byte cap")
    raw = path.read_bytes()
    if b"\r\n" in raw:
        raise VolkersenAuditError("CRLF_FORBIDDEN", "CRLF line endings forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VolkersenAuditError("INVALID_UTF8", f"invalid UTF-8: {exc}") from exc
    parsed = parse_strict_json(text)
    canonical_raw = canonical_json_bytes(parsed)
    if raw not in (canonical_raw, canonical_raw + b"\n"):
        raise VolkersenAuditError("NON_CANONICAL_CERT_BYTES", "certificate file is not canonical JSON")
    return verify_certificate_dict(parsed)


def write_certificate_file(path: Path) -> dict[str, Any]:
    _require_no_symlink_path(path)
    cert = build_certificate()
    raw = canonical_json_bytes(cert) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return check_certificate_file(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact-Q Volkersen anchor shear-lag checker for cfrp-truss")
    parser.add_argument("--standalone", action="store_true", help="Run full standalone exact verification")
    parser.add_argument("--json", action="store_true", help="Emit canonical JSON certificate to stdout")
    parser.add_argument("--write", nargs="?", const=str(DEFAULT_CERT_PATH), help="Write canonical certificate")
    parser.add_argument("--check", type=str, help="Fail-closed verification of a certificate JSON file")
    args = parser.parse_args(argv)

    try:
        if args.check is not None:
            res = check_certificate_file(Path(args.check))
            print(
                f"OK {CERT_SCHEMA} payload_sha256={res['payload_sha256']} "
                f"certificate_sha256={res['certificate_sha256']}"
            )
            return 0
        if args.write is not None:
            res = write_certificate_file(Path(args.write))
            print(
                f"WROTE {CERT_SCHEMA} payload_sha256={res['payload_sha256']} "
                f"certificate_sha256={res['certificate_sha256']}"
            )
            return 0
        cert = build_certificate()
        if args.json:
            sys.stdout.buffer.write(canonical_json_bytes(cert) + b"\n")
            return 0
        print(
            f"OK {CERT_SCHEMA} payload_sha256={cert['payload_sha256']} "
            f"certificate_sha256={cert['certificate_sha256']} "
            f"all_passed={cert['payload']['all_passed']}"
        )
        return 0
    except (VolkersenAuditError, ValueError, RecursionError, OSError) as exc:
        print(f"REJECT {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
