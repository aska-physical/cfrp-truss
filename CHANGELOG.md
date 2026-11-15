# Changelog

## [0.2.0] - 2026-11-15
### Added
- Resolved Issue #1: implemented exact coupled cracked-section serviceability (`ACI 440.2R-17 §10.2.8`: `0.60 fc'` total concrete, `0.45 fc'` sustained concrete, `0.80 fy` steel) and creep-rupture (`§10.2.9`: `0.55 ffu` CFRP) solver in `verify.py` with initial dead-load soffit strain `eps_bi` (`solve_unstrengthened_cracked_eps_bi`), active pre-strain `eps_pre_cfrp`, and SymPy symbolic transformed-section identity verification.
- Proved that heavy service overload (`M_s = 260 kN·m`, `M_u = 382 kN·m`) passes ULS flexure (`433.45 kN·m`) yet fails passive concrete serviceability (`21.639 > 21.0 MPa`), and demonstrated that `3,200 microstrain` pre-strain anchored into the `45-deg` truss stitches restores full serviceability (`fc_s = 18.771 MPa`, `fs_s = 230.832 MPa`, `ff_s = 808.733 <= 2210.306 MPa`).
- Expanded `must_fail_controls` from 5 to 8 (`uls_passing_heavy_overload_beam_passes_serviceability_without_check`, `excessive_cfrp_sustained_stress_passes_0.55ffu_creep_rupture_gate`, `omitting_dead_load_eps_bi_is_conservative_for_rebar_stress`) and regenerated `results/certificate.json` (`v0.2.0`).

## [0.1.0] - 2026-11-14
### Added
- Initialized `aska-physical/cfrp-truss` baseline (`README.md`, `HANDOFF.md`, `CHANGELOG.md`, `verify.py`, `results/certificate.json`).
- Implemented deterministic SI `ACI 440.2R-17/-23` flexural equilibrium solver comparing unanchored EBR flat strips against NSM + $45^\circ$ truss-stitched T1100G CFRP retrofit kits (`+77.34%` moment gain at equal area; higher capacity at `50%` lower CFRP mass).
- Added $45^\circ$ shear-stitch resistance check (`eps_fv <= 0.004`) and 4-wire Cole EIS strain/moisture/debonding separability verification with 5 must-fail controls.
