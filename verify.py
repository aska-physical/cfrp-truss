#!/usr/bin/env python3
"""Deterministic ACI 440.2R-17/-23 ULS + Serviceability (§10.2.8) + Creep (§10.2.9)
+ ACI 318M-19 Ch. 17 45-deg Dowel/Epoxy Sizing + Cole EIS verifier (v0.3.0)."""

from __future__ import annotations
import argparse, cmath, errno, hmac, json, math, os, secrets, stat, sys, threading
from pathlib import Path
from typing import Any, Sequence
import sympy as sp

ROOT = Path(__file__).resolve().parent
CERT_PATH = ROOT / "results" / "certificate.json"
MAX_CERT_BYTES = 1_048_576
HARD_MAX_BYTES_CAP = 64 * 1024 * 1024
_EXACT_PATH_TYPE = type(Path("."))
_READ_CHUNK_BYTES = 65536
_TMP_LOCK = threading.Lock()
_TMP_COUNTER = 0
_TMP_MAX_ATTEMPTS = 32


def require(condition: bool, message: str) -> None:
    """Fail-closed guard that cannot be stripped by python3 -O."""
    if not condition:
        raise ValueError(f"verification failed: {message}")


def require_finite(value: Any, name: str) -> float:
    """Fail closed on bool, subclasses, non-numeric, OverflowError (e.g. 10**4300), NaN, or Inf."""
    if type(value) not in (int, float):
        raise ValueError(f"verification failed: {name} must be a finite real number")
    try:
        val = float(value)
    except OverflowError as exc:
        raise ValueError(f"verification failed: {name} overflows IEEE-754 float64") from exc
    if not math.isfinite(val):
        raise ValueError(f"verification failed: {name} must be finite (got {val!r})")
    return val


def require_positive_int(value: Any, name: str) -> int:
    """Fail closed on bool, non-exact-int, <= 0, or float64-overflowing integer counts."""
    if type(value) is not int:
        raise ValueError(f"verification failed: {name} must be a positive int")
    require_finite(value, name)
    require(value > 0, f"{name} must be > 0 (got {value})")
    return value


def finite_min(a: float, b: float, name: str = "value") -> float:
    """NaN/Inf-safe replacement for builtin min(a, b)."""
    fa = require_finite(a, f"{name}.a")
    fb = require_finite(b, f"{name}.b")
    return fa if fa <= fb else fb


def finite_max(a: float, b: float, name: str = "value") -> float:
    """NaN/Inf-safe replacement for builtin max(a, b)."""
    fa = require_finite(a, f"{name}.a")
    fb = require_finite(b, f"{name}.b")
    return fa if fa >= fb else fb


def finite_clamp(x: float, lo: float, hi: float, name: str = "value") -> float:
    """NaN/Inf-safe replacement for clamping x into [lo, hi]."""
    fx = require_finite(x, name)
    flo = require_finite(lo, f"{name}.lo")
    fhi = require_finite(hi, f"{name}.hi")
    require(flo <= fhi, f"{name} clamp bounds inverted: {flo} > {fhi}")
    if fx < flo:
        return flo
    if fx > fhi:
        return fhi
    return fx


def finite_max_seq(values: list[float], name: str = "values") -> float:
    """NaN/Inf-safe replacement for builtin max(seq)."""
    require(len(values) > 0, f"{name} must be non-empty")
    best = require_finite(values[0], f"{name}[0]")
    for idx in range(1, len(values)):
        cur = require_finite(values[idx], f"{name}[{idx}]")
        if cur > best:
            best = cur
    return best


def render_certificate_bytes(cert: dict[str, Any]) -> bytes:
    """Render deterministic canonical UTF-8 LF JSON bytes matching results/certificate.json."""
    return (json.dumps(cert, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _require_utf8_path_str(raw: str, label: str) -> None:
    if "\x00" in raw:
        raise ValueError(f"{label} contains NUL byte")
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} contains lone UTF-16 surrogate") from exc


def _step_dir_fd_chain(start_fd: int, components: Sequence[str], label: str, *, create_missing: bool = False) -> int:
    """Atomically traverse directory components via openat(O_RDONLY|O_DIRECTORY|O_NOFOLLOW)."""
    dfd = start_fd
    for comp in components:
        try:
            nxt = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        except FileNotFoundError as exc:
            if create_missing:
                try:
                    os.mkdir(comp, 0o700, dir_fd=dfd)
                    nxt = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
                except OSError as exc2:
                    os.close(dfd)
                    raise ValueError(f"cannot create {label} directory: {type(exc2).__name__}") from exc2
            else:
                os.close(dfd)
                raise ValueError(f"missing directory component at {label}") from exc
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                try:
                    is_sym = stat.S_ISLNK(os.lstat(comp, dir_fd=dfd).st_mode)
                except OSError:
                    is_sym = True
                os.close(dfd)
                if is_sym:
                    raise ValueError(f"symlink forbidden at {label}") from exc
                raise ValueError(f"non-directory ancestor at {label}") from exc
            os.close(dfd)
            raise ValueError(f"cannot traverse {label}: {type(exc).__name__}") from exc
        os.close(dfd)
        dfd = nxt
    return dfd


_DARWIN_SYSTEM_ROOT_ALIASES: tuple[tuple[str, str], ...] = (
    ("/var", "/private/var"),
    ("/tmp", "/private/tmp"),
    ("/etc", "/private/etc"),
)


def _normalize_darwin_system_prefix(path: Path, *, allow_exact: bool = False) -> Path:
    """Map verified root-owned macOS /var, /tmp, /etc aliases to /private/* without resolving user symlinks."""
    if sys.platform != "darwin" or not path.is_absolute():
        return path
    parts = path.parts
    min_parts = 2 if allow_exact else 3
    if len(parts) < min_parts or parts[0] != "/" or ".." in parts:
        return path
    top = "/" + parts[1]
    for alias, canonical in _DARWIN_SYSTEM_ROOT_ALIASES:
        if top != alias:
            continue
        try:
            st = os.lstat(alias)
            if not stat.S_ISLNK(st.st_mode) or st.st_uid != 0:
                return path
            target = os.readlink(alias)
            if target not in (canonical, canonical.lstrip("/")):
                return path
        except OSError:
            return path
        return Path(canonical, *parts[2:])
    return path


def _check_pwd_no_symlinks() -> None:
    """Verify logical PWD ancestors contain zero symlinks via dir_fd chain (zero islink)."""
    pwd_env = os.environ.get("PWD", "")
    if not pwd_env.startswith("/"):
        return
    _require_utf8_path_str(pwd_env, "PWD")
    pwd_path = _normalize_darwin_system_prefix(Path(pwd_env), allow_exact=True)
    pwd_parts = [p for p in pwd_path.parts[1:] if p not in ("", ".")]
    try:
        root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise ValueError(f"cannot open root for PWD check: {type(exc).__name__}") from exc
    end_fd = _step_dir_fd_chain(root_fd, pwd_parts, "pwd_ancestor")
    os.close(end_fd)


def _open_parent_dir_fd(path: Any, *, create_missing: bool = False) -> tuple[int, str]:
    """Validate exact Path type and atomically pin parent dir_fd against TOCTOU swaps."""
    if type(path) is not _EXACT_PATH_TYPE:
        raise ValueError("path subclass or non-Path forbidden")
    _require_utf8_path_str(str(path), "path")
    if path.is_absolute():
        norm_path = _normalize_darwin_system_prefix(path)
        raw_parts = [p for p in norm_path.parts[1:] if p not in ("", ".")]
        try:
            start_fd = os.open(norm_path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as exc:
            raise ValueError(f"cannot open root anchor: {type(exc).__name__}") from exc
    else:
        try:
            os.getcwd()
            start_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
        except OSError as exc:
            raise ValueError(f"current working directory unavailable: {type(exc).__name__}") from exc
        try:
            _check_pwd_no_symlinks()
        except ValueError:
            os.close(start_fd)
            raise
        raw_parts = [p for p in path.parts if p not in ("", ".")]

    if not raw_parts or raw_parts[-1] == "..":
        os.close(start_fd)
        raise ValueError("certificate path must be a regular file, not a directory")

    dfd = _step_dir_fd_chain(start_fd, raw_parts[:-1], "path_component", create_missing=create_missing)
    return dfd, raw_parts[-1]


def verify_canonical_cert_bytes(
    path: Path,
    expected_bytes: bytes,
    *,
    max_bytes: int = MAX_CERT_BYTES,
) -> tuple[bool, str]:
    """Fail-closed Zone-B verification: dir_fd pinned non-symlink regular file, streaming capped read, constant-time match."""
    if type(expected_bytes) is not bytes:
        return False, "expected_bytes must be exact bytes"
    try:
        cap = require_positive_int(max_bytes, "max_bytes")
    except ValueError as exc:
        return False, str(exc)
    if cap > HARD_MAX_BYTES_CAP:
        return False, f"max_bytes exceeds {HARD_MAX_BYTES_CAP}"
    try:
        dfd, leaf = _open_parent_dir_fd(path)
    except ValueError as exc:
        return False, str(exc)
    fd = -1
    try:
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return False, "symlink forbidden at leaf"
            return False, f"cannot open certificate: {type(exc).__name__}"
        st_fd = os.fstat(fd)
        if not stat.S_ISREG(st_fd.st_mode):
            return False, "certificate path must be a regular file"
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                return False, f"certificate exceeds {cap} bytes"
            chunks.append(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        return False, f"cannot read certificate: {type(exc).__name__}"
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(dfd)
    if b"\r" in raw:
        return False, "CR/CRLF line endings forbidden"
    if not hmac.compare_digest(raw, expected_bytes):
        return False, "certificate bytes do not match canonical verification output"
    return True, "ok"


def write_canonical_cert_bytes(path: Path, payload_bytes: bytes) -> tuple[bool, str]:
    """Atomic 0o600 dir_fd writer refusing symlinks, hardlinks (st_nlink > 1), and non-regular targets."""
    global _TMP_COUNTER
    if type(payload_bytes) is not bytes:
        return False, "payload_bytes must be exact bytes"
    try:
        dfd, leaf = _open_parent_dir_fd(path, create_missing=True)
    except ValueError as exc:
        return False, str(exc)
    tmp_fd = -1
    created_tmp = False
    tmp_name = ""
    try:
        try:
            st = os.lstat(leaf, dir_fd=dfd)
        except FileNotFoundError:
            st = None
        except OSError as exc:
            return False, f"cannot stat target: {type(exc).__name__}"
        if st is not None:
            if stat.S_ISLNK(st.st_mode):
                return False, "symlink forbidden at write target"
            if not stat.S_ISREG(st.st_mode):
                return False, "refusing to overwrite non-regular target"
            if st.st_nlink > 1:
                return False, "refusing to overwrite hardlinked target"
        for _ in range(_TMP_MAX_ATTEMPTS):
            with _TMP_LOCK:
                _TMP_COUNTER += 1
                seq = _TMP_COUNTER
            cand = f".cfrp_truss_tmp_{os.getpid()}_{seq}_{secrets.token_hex(8)}"
            try:
                tmp_fd = os.open(
                    cand,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                    dir_fd=dfd,
                )
                tmp_name = cand
                created_tmp = True
                break
            except FileExistsError:
                continue
        if not created_tmp:
            return False, "exhausted atomic temp file retries"
        view = memoryview(payload_bytes)
        written = 0
        while written < len(payload_bytes):
            written += os.write(tmp_fd, view[written:])
        os.fsync(tmp_fd)
        if os.fstat(tmp_fd).st_nlink != 1:
            return False, "temp file hardlinked before replace"
        os.replace(tmp_name, leaf, src_dir_fd=dfd, dst_dir_fd=dfd)
        created_tmp = False
        os.close(tmp_fd)
        tmp_fd = -1
        os.fsync(dfd)
        return True, "ok"
    except OSError as exc:
        return False, f"cannot write certificate: {type(exc).__name__}"
    finally:
        if tmp_fd >= 0:
            os.close(tmp_fd)
        if created_tmp:
            try:
                os.unlink(tmp_name, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)


def aci440_ebr_debonding_strain(fc_MPa: float, n_plies: int, Ef_MPa: float, tf_mm: float, eps_fu: float) -> float:
    fc = require_finite(fc_MPa, "fc_MPa")
    n_p = float(require_positive_int(n_plies, "n_plies"))
    ef = require_finite(Ef_MPa, "Ef_MPa")
    tf = require_finite(tf_mm, "tf_mm")
    efu = require_finite(eps_fu, "eps_fu")
    require(fc > 0.0 and ef > 0.0 and tf > 0.0 and efu > 0.0, "EBR debonding inputs must be > 0")
    return finite_min(0.41 * math.sqrt(fc / (n_p * ef * tf)), 0.9 * efu, "ebr_debonding_strain")


def solve_unstrengthened_cracked_eps_bi(
    M_DL_kNm: float, b_mm: float, d_mm: float, df_mm: float, fc_MPa: float, As_mm2: float, Es_MPa: float
) -> dict[str, float]:
    m_dl = require_finite(M_DL_kNm, "M_DL_kNm")
    b = require_finite(b_mm, "b_mm")
    d = require_finite(d_mm, "d_mm")
    df = require_finite(df_mm, "df_mm")
    fc = require_finite(fc_MPa, "fc_MPa")
    as_m = require_finite(As_mm2, "As_mm2")
    es = require_finite(Es_MPa, "Es_MPa")
    require(b > 0.0 and d > 0.0 and fc > 0.0 and as_m > 0.0 and es > 0.0, "cracked section inputs must be > 0")
    Ec = 4700.0 * math.sqrt(fc)
    ns = es / Ec
    rho = as_m / (b * d)
    ck0 = (math.sqrt((rho * ns) ** 2 + 2.0 * rho * ns) - rho * ns) * d
    Icr0 = (b * (ck0**3)) / 3.0 + ns * as_m * ((d - ck0) ** 2)
    eps_bi = (m_dl * 1e6 * (df - ck0)) / (Ec * Icr0)
    return {"Ec_MPa": round(Ec, 2), "ck0_mm": round(ck0, 4), "Icr0_mm4": round(Icr0, 1), "eps_bi": round(eps_bi, 6)}


def solve_cracked_serviceability(
    M_service_kNm: float, M_sustained_kNm: float, b_mm: float, d_mm: float, df_mm: float,
    fc_MPa: float, As_mm2: float, fy_MPa: float, Es_MPa: float, Af_mm2: float,
    Ef_MPa: float, ffu_design_MPa: float, eps_bi: float, eps_pre_cfrp: float = 0.0,
    creep_factor_cfrp: float = 0.55,
) -> dict[str, Any]:
    for val, nm in (
        (M_service_kNm, "M_service_kNm"), (M_sustained_kNm, "M_sustained_kNm"),
        (b_mm, "b_mm"), (d_mm, "d_mm"), (df_mm, "df_mm"), (fc_MPa, "fc_MPa"),
        (As_mm2, "As_mm2"), (fy_MPa, "fy_MPa"), (Es_MPa, "Es_MPa"),
        (Af_mm2, "Af_mm2"), (Ef_MPa, "Ef_MPa"), (ffu_design_MPa, "ffu_design_MPa"),
        (eps_bi, "eps_bi"), (eps_pre_cfrp, "eps_pre_cfrp"), (creep_factor_cfrp, "creep_factor_cfrp"),
    ):
        require_finite(val, nm)
    require(fc_MPa > 0.0 and b_mm > 0.0 and d_mm > 0.0, "serviceability section inputs must be > 0")
    Ec = 4700.0 * math.sqrt(fc_MPa)

    def solve_at_moment(M_kNm: float) -> tuple[float, float, float, float]:
        M_target = M_kNm * 1e6

        def moment_for_ck(ck: float) -> tuple[float, float, float, float]:
            lo, hi = 0.0, 2.0e-4
            for _ in range(65):
                pm = 0.5 * (lo + hi)
                fs = Es_MPa * pm * (d_mm - ck)
                ff = Ef_MPa * finite_max(0.0, pm * (df_mm - ck) - eps_bi + eps_pre_cfrp, "eps_f_serv")
                if As_mm2 * fs * (d_mm - ck / 3.0) + Af_mm2 * ff * (df_mm - ck / 3.0) < M_target:
                    lo = pm
                else:
                    hi = pm
            ps = 0.5 * (lo + hi)
            fc = Ec * ps * ck
            fs = Es_MPa * ps * (d_mm - ck)
            ff = Ef_MPa * finite_max(0.0, ps * (df_mm - ck) - eps_bi + eps_pre_cfrp, "eps_f_serv")
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
    for val, nm in (
        (b_mm, "b_mm"), (d_mm, "d_mm"), (h_mm, "h_mm"), (fc_MPa, "fc_MPa"),
        (As_mm2, "As_mm2"), (fy_MPa, "fy_MPa"), (Es_MPa, "Es_MPa"),
        (Af_mm2, "Af_mm2"), (Ef_MPa, "Ef_MPa"), (eps_fd_cap, "eps_fd_cap"),
        (eps_bi, "eps_bi"), (psi_f, "psi_f"),
    ):
        require_finite(val, nm)
    require(fc_MPa > 0.0 and b_mm > 0.0 and d_mm > 0.0 and h_mm > 0.0, "beam geometry/strength inputs must be > 0")
    eps_cu = 0.003
    beta1 = finite_clamp(0.85 - 0.05 * (fc_MPa - 28.0) / 7.0, 0.65, 0.85, "beta1")

    def force_residual(c_mm: float) -> tuple[float, float, float, float]:
        eps_fe = finite_min(eps_fd_cap, eps_cu * (h_mm - c_mm) / c_mm - eps_bi, "eps_fe")
        eps_c = (eps_fe + eps_bi) * c_mm / (h_mm - c_mm)
        eps_s = (eps_fe + eps_bi) * (d_mm - c_mm) / (h_mm - c_mm)
        fs = finite_min(fy_MPa, Es_MPa * eps_s, "fs")
        ff = Ef_MPa * eps_fe
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
    fs = finite_min(fy_MPa, Es_MPa * eps_s, "fs")
    ff = Ef_MPa * eps_fe
    Mn_s = As_mm2 * fs * (d_mm - 0.5 * b1 * c_sol) * 1e-6
    Mn_f = Af_mm2 * ff * (h_mm - 0.5 * b1 * c_sol) * 1e-6
    eps_sy = fy_MPa / Es_MPa
    phi = 0.90 if eps_s >= 0.005 else (0.65 if eps_s <= eps_sy else 0.65 + 0.25 * (eps_s - eps_sy) / (0.005 - eps_sy))
    return {
        "c_mm": round(require_finite(c_sol, "c_sol"), 4),
        "eps_fe": round(require_finite(eps_fe, "eps_fe"), 6),
        "eps_s": round(require_finite(eps_s, "eps_s"), 6),
        "phi": round(require_finite(phi, "phi"), 4),
        "phi_Mn_kNm": round(require_finite(phi * (Mn_s + psi_f * Mn_f), "phi_Mn_kNm"), 3),
        "ff_MPa": round(require_finite(ff, "ff"), 2),
    }


def solve_chord_shear_flow_fd(
    M_nominal_peak_kNm: float, L_shear_mm: float, n_steps: int, b_mm: float, d_mm: float, df_mm: float,
    fc_MPa: float, As_mm2: float, fy_MPa: float, Es_MPa: float, Af_mm2: float, Ef_MPa: float,
    eps_bi: float, eps_pre_cfrp: float, n_grooves: int = 2, groove_w_mm: float = 12.0, groove_d_mm: float = 20.0,
) -> dict[str, Any]:
    for val, nm in (
        (M_nominal_peak_kNm, "M_nominal_peak_kNm"), (L_shear_mm, "L_shear_mm"),
        (b_mm, "b_mm"), (d_mm, "d_mm"), (df_mm, "df_mm"), (fc_MPa, "fc_MPa"),
        (As_mm2, "As_mm2"), (fy_MPa, "fy_MPa"), (Es_MPa, "Es_MPa"),
        (Af_mm2, "Af_mm2"), (Ef_MPa, "Ef_MPa"), (eps_bi, "eps_bi"),
        (eps_pre_cfrp, "eps_pre_cfrp"), (groove_w_mm, "groove_w_mm"), (groove_d_mm, "groove_d_mm"),
    ):
        require_finite(val, nm)
    n_steps = require_positive_int(n_steps, "n_steps")
    n_grooves = require_positive_int(n_grooves, "n_grooves")
    require(fc_MPa > 0.0 and L_shear_mm > 0.0, "chord shear flow inputs must be > 0")
    Ec = 4700.0 * math.sqrt(fc_MPa)
    eps_c0 = 1.7 * fc_MPa / Ec

    def chord_tension(M_kNm: float) -> float:
        if M_kNm <= 1e-6:
            return Af_mm2 * Ef_MPa * finite_max(0.0, eps_pre_cfrp - eps_bi, "eps_pre_net")
        M_t = M_kNm * 1e6

        def mf_for_c(c_mm: float) -> tuple[float, float]:
            lo, hi = 0.0, 3.0e-4
            for _ in range(65):
                pm = 0.5 * (lo + hi)
                fs = finite_min(fy_MPa, Es_MPa * pm * (d_mm - c_mm), "fs")
                ff = Ef_MPa * finite_max(0.0, pm * (df_mm - c_mm) - eps_bi + eps_pre_cfrp, "eps_f")
                r = finite_min(1.95, (pm * c_mm) / eps_c0, "r")
                b1 = (4.0 - r) / (6.0 - 2.0 * r) if r > 1e-5 else 0.6667
                if As_mm2 * fs * (d_mm - 0.5 * b1 * c_mm) + Af_mm2 * ff * (df_mm - 0.5 * b1 * c_mm) < M_t:
                    lo = pm
                else:
                    hi = pm
            ps = 0.5 * (lo + hi)
            ec = ps * c_mm
            r = finite_min(1.95, ec / eps_c0, "r")
            if r <= 1e-5:
                Cc = 0.5 * b_mm * c_mm * Ec * ec
            else:
                b1 = (4.0 - r) / (6.0 - 2.0 * r)
                Cc = ((3.0 * eps_c0 * ec - ec**2) / (3.0 * b1 * eps_c0**2)) * fc_MPa * b1 * c_mm * b_mm
            fs = finite_min(fy_MPa, Es_MPa * ps * (d_mm - c_mm), "fs")
            Tf = Af_mm2 * Ef_MPa * finite_max(0.0, ps * (df_mm - c_mm) - eps_bi + eps_pre_cfrp, "eps_f")
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
    qf_avg, qf_peak, qf_pre = (Tf_l[-1] - Tf_l[0]) / L_shear_mm, finite_max_seq(qf_l, "qf_l"), qf_l[int(0.25 * n_steps)]
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
        "qf_excess_carried_by_stitches_N_per_mm": round(finite_max(0.0, qf_peak - tau_cap * p_gr, "qf_excess"), 3),
    }


def evaluate_aci318_ch17_anchor_group(
    n_anchors: int, n_rows_across_width: int, s_long_mm: float, s_trans_mm: float, c_end_mm: float,
    b_beam_mm: float, da_mm: float, L_drill_mm: float, theta_deg: float, fc_MPa: float,
    tau_cr_2500psi_MPa: float, tau_uncr_2500psi_MPa: float, F_horiz_ult_kN: float, F_horiz_sus_kN: float,
    engage_closed_stirrup_core: bool = True, stirrup_spacing_mm: float = 150.0,
    Asv_2leg_mm2: float = 157.0, fyt_MPa: float = 420.0, pad_friction_coeff: float = 0.40,
) -> dict[str, Any]:
    for val, nm in (
        (s_long_mm, "s_long_mm"), (s_trans_mm, "s_trans_mm"), (c_end_mm, "c_end_mm"),
        (b_beam_mm, "b_beam_mm"), (da_mm, "da_mm"), (L_drill_mm, "L_drill_mm"),
        (theta_deg, "theta_deg"), (fc_MPa, "fc_MPa"),
        (tau_cr_2500psi_MPa, "tau_cr_2500psi_MPa"), (tau_uncr_2500psi_MPa, "tau_uncr_2500psi_MPa"),
        (F_horiz_ult_kN, "F_horiz_ult_kN"), (F_horiz_sus_kN, "F_horiz_sus_kN"),
        (stirrup_spacing_mm, "stirrup_spacing_mm"), (Asv_2leg_mm2, "Asv_2leg_mm2"),
        (fyt_MPa, "fyt_MPa"), (pad_friction_coeff, "pad_friction_coeff"),
    ):
        require_finite(val, nm)
    n_anchors = require_positive_int(n_anchors, "n_anchors")
    n_rows_across_width = require_positive_int(n_rows_across_width, "n_rows_across_width")
    require(n_anchors >= n_rows_across_width, "n_anchors must be >= n_rows_across_width")
    require(fc_MPa > 0.0 and da_mm > 0.0 and L_drill_mm > 0.0 and stirrup_spacing_mm > 0.0, "anchor geometry/strength inputs must be > 0")
    rad = math.radians(theta_deg)
    hef_geom = L_drill_mm * math.sin(rad) if theta_deg > 1e-3 else L_drill_mm
    hef = finite_min(20.0 * da_mm, hef_geom, "hef")
    f_sc = (finite_clamp(fc_MPa, 17.2, 55.2, "fc_MPa") / 17.2) ** 0.25
    tau_cr, tau_uncr = tau_cr_2500psi_MPa * f_sc, tau_uncr_2500psi_MPa * f_sc
    phi_N, phi_V, phi_s = 0.65, 0.70, 0.75

    Nb = 7.0 * math.sqrt(fc_MPa) * (hef**1.5)
    ANco = 9.0 * (hef**2)
    n_long = n_anchors // n_rows_across_width
    c_side = 0.5 * (b_beam_mm - (n_rows_across_width - 1) * s_trans_mm)
    w_nc = finite_min(
        b_beam_mm,
        2.0 * finite_min(c_side, 1.5 * hef, "c_side_nc") + (n_rows_across_width - 1) * finite_min(s_trans_mm, 3.0 * hef, "s_trans_nc"),
        "w_nc",
    )
    L_zone = (n_long - 1) * s_long_mm
    L_nc = finite_min(c_end_mm, 1.5 * hef, "c_end_nc") + (n_long - 1) * finite_min(s_long_mm, 3.0 * hef, "s_long_nc") + 1.5 * hef
    ca_min = finite_min(c_side, c_end_mm, "ca_min")
    psi_ed_N = 1.0 if ca_min >= 1.5 * hef else (0.7 + 0.3 * (ca_min / (1.5 * hef)))
    Ncbg = ((w_nc * L_nc) / ANco) * psi_ed_N * Nb
    phi_Ncbg_kN = phi_N * Ncbg * 1e-3

    if engage_closed_stirrup_core and hef_geom >= 120.0:
        n_st = int(finite_max(1.0, float(math.floor((L_zone + hef_geom) / stirrup_spacing_mm) + 1), "n_st"))
        phi_N_st_kN = phi_s * n_st * Asv_2leg_mm2 * fyt_MPa * 1e-3
        phi_N_up_kN = phi_N_st_kN
    else:
        phi_N_st_kN, phi_N_up_kN = 0.0, phi_Ncbg_kN

    Nba = tau_cr * math.pi * da_mm * hef
    cNa = 10.0 * da_mm * math.sqrt(tau_uncr / 7.6)
    w_na = finite_min(
        b_beam_mm,
        2.0 * finite_min(c_side, cNa, "c_side_na") + (n_rows_across_width - 1) * finite_min(s_trans_mm, 2.0 * cNa, "s_trans_na"),
        "w_na",
    )
    L_na = finite_min(c_end_mm, cNa, "c_end_na") + (n_long - 1) * finite_min(s_long_mm, 2.0 * cNa, "s_long_na") + cNa
    psi_ed_Na = 1.0 if ca_min >= cNa else (0.7 + 0.3 * (ca_min / cNa))
    Nag = ((w_na * L_na) / ((2.0 * cNa) ** 2)) * psi_ed_Na * Nba
    phi_Nag_kN = phi_N * Nag * 1e-3

    k_cp = 2.0 if hef >= 65.0 else 1.0
    gov_N_pry = Nag if (engage_closed_stirrup_core and hef_geom >= 120.0) else finite_min(Ncbg, Nag, "gov_N_pry")
    phi_Vcpg_kN = phi_V * k_cp * gov_N_pry * 1e-3

    if theta_deg < 89.0:
        den = math.cos(rad) + pad_friction_coeff * math.sin(rad)
        T_ult, T_sus, V_guil = F_horiz_ult_kN / den, F_horiz_sus_kN / den, 0.0
        N_up = T_ult * math.sin(rad)
        V_pry = T_ult * math.cos(rad) * (1.0 - pad_friction_coeff * math.tan(rad))
    else:
        T_ult, T_sus, V_guil, N_up, V_pry = 0.0, 0.0, F_horiz_ult_kN, 0.0, F_horiz_ult_kN

    u_up = require_finite(N_up / phi_N_up_kN, "u_up")
    u_bd = require_finite(T_ult / phi_Nag_kN, "u_bd")
    u_pr = require_finite(finite_max(0.0, V_pry, "V_pry") / phi_Vcpg_kN, "u_pr")
    inter = require_finite((finite_max(u_up, u_bd, "u_tension") ** (5.0 / 3.0)) + (u_pr ** (5.0 / 3.0)), "interaction_5_3")
    cap_sus_1, sus_1 = 0.55 * phi_N * Nba * 1e-3, T_sus / n_anchors
    pass_cr = sus_1 <= cap_sus_1

    Ad = 0.25 * math.pi * (da_mm**2)
    pass_mat = (T_ult <= n_anchors * 0.75 * Ad * 4018.74 * 1e-3) and (V_guil <= n_anchors * 0.65 * Ad * 75.0 * 1e-3)
    tau_sl = (T_ult * 1e3) / (n_anchors * math.pi * da_mm * finite_max(4.0 * da_mm, L_drill_mm - 20.0, "slant_length"))
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
    n_grooves = require_positive_int(n_grooves, "n_grooves")
    n_nozzles = require_positive_int(n_nozzles, "n_nozzles")
    for val, nm in (
        (L_beam_mm, "L_beam_mm"), (groove_w_mm, "groove_w_mm"),
        (groove_d_mm, "groove_d_mm"), (Af_total_mm2, "Af_total_mm2"), (n_dist_stitches, "n_dist_stitches"),
        (L_dist_drill_mm, "L_dist_drill_mm"), (da_dist_mm, "da_dist_mm"), (dhole_dist_mm, "dhole_dist_mm"),
        (n_end_anchors_total, "n_end_anchors_total"), (L_end_drill_mm, "L_end_drill_mm"),
        (da_end_mm, "da_end_mm"), (dhole_end_mm, "dhole_end_mm"), (cartridge_vol_mL, "cartridge_vol_mL"),
        (waste_overbore_factor, "waste_overbore_factor"), (nozzle_purge_mL, "nozzle_purge_mL"),
    ):
        require_finite(val, nm)
    require(cartridge_vol_mL > 0.0, "cartridge_vol_mL must be > 0")
    try:
        v_gr = require_finite(L_beam_mm * (n_grooves * groove_w_mm * groove_d_mm - Af_total_mm2) * 1e-3 * waste_overbore_factor, "v_gr")
        a_d = require_finite(0.25 * math.pi * (dhole_dist_mm**2 - da_dist_mm**2), "a_d")
        a_e = require_finite(0.25 * math.pi * (dhole_end_mm**2 - da_end_mm**2), "a_e")
        v_st = require_finite((n_dist_stitches * L_dist_drill_mm * a_d + n_end_anchors_total * L_end_drill_mm * a_e) * 1e-3 * waste_overbore_factor, "v_st")
        v_p = require_finite(n_nozzles * nozzle_purge_mL, "v_p")
        tot = require_finite(v_gr + v_st + v_p, "total_mixed_epoxy_mL")
        cart_ratio = require_finite(tot / cartridge_vol_mL, "dual_cartridges_ratio")
    except OverflowError as exc:
        raise ValueError(f"verification failed: epoxy volume overflow: {exc}") from exc
    return {
        "groove_epoxy_mL": round(v_gr, 1), "dowel_holes_epoxy_mL": round(v_st, 1),
        "static_nozzle_purge_mL": round(v_p, 1), "total_mixed_epoxy_mL": round(tot, 1),
        "cartridge_size_mL": cartridge_vol_mL, "dual_cartridges_per_6m_beam": math.ceil(cart_ratio),
    }


def shear_stitch_contribution_kN(A_stitch_mm2: float, Ef_MPa: float, d_fv_mm: float, s_mm: float, theta_deg: float = 45.0) -> dict[str, float]:
    for val, nm in (
        (A_stitch_mm2, "A_stitch_mm2"), (Ef_MPa, "Ef_MPa"),
        (d_fv_mm, "d_fv_mm"), (s_mm, "s_mm"), (theta_deg, "theta_deg"),
    ):
        require_finite(val, nm)
    require(s_mm > 0.0, "s_mm must be > 0")
    rad = math.radians(theta_deg)
    Vf = (A_stitch_mm2 * Ef_MPa * 0.004 * (math.sin(rad) + math.cos(rad)) * d_fv_mm) / s_mm
    Vf_kN = require_finite(Vf * 1e-3, "Vf_45deg_kN")
    return {"eps_fv_cap": 0.004, "Vf_45deg_kN": round(Vf_kN, 3)}


def cole_bondline_eis(freqs_Hz: list[float], R_inf: float, R0: float, tau_s: float, alpha: float) -> list[tuple[float, float]]:
    for val, nm in ((R_inf, "R_inf"), (R0, "R0"), (tau_s, "tau_s"), (alpha, "alpha")):
        require_finite(val, nm)
    require(tau_s > 0.0, "tau_s must be > 0")
    require(0.0 < alpha <= 1.0, f"alpha must be in (0, 1] (got {alpha})")
    checked_freqs = [require_finite(f, "freqs_Hz") for f in freqs_Hz]
    require(all(f > 0.0 for f in checked_freqs), "freqs_Hz entries must be > 0")
    out: list[tuple[float, float]] = []
    for f in checked_freqs:
        omega_tau = require_finite(2.0 * math.pi * f * tau_s, "omega_tau")
        require(omega_tau > 0.0, "omega_tau must be > 0 without underflow")
        try:
            z = R_inf + (R0 - R_inf) / (1.0 + cmath.exp(alpha * cmath.log(complex(0.0, omega_tau))))
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError(f"verification failed: cole_bondline_eis numerical overflow: {exc}") from exc
        out.append((require_finite(z.real, "z.real"), require_finite(z.imag, "z.imag")))
    return out


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
    expected_bytes = render_certificate_bytes(cert)
    if args.write:
        ok_w, reason_w = write_canonical_cert_bytes(CERT_PATH, expected_bytes)
        if not ok_w:
            print(f"[FAIL] results/certificate.json write refused: {reason_w}", file=sys.stderr)
            return 1
    ok_v, reason_v = verify_canonical_cert_bytes(CERT_PATH, expected_bytes)
    if not ok_v:
        print(f"[FAIL] results/certificate.json mismatch or missing: {reason_v}", file=sys.stderr)
        return 1
    n_controls = len(cert["must_fail_controls"])
    print(f"[PASS] cfrp-truss v0.3.0 ULS + Serviceability + Creep + ACI 318 Ch.17 Dowel/Epoxy + EIS ({n_controls}/{n_controls} controls): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
