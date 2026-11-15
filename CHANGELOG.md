# Changelog

## [0.1.0] - 2026-11-14
### Added
- Initialized `aska-physical/cfrp-truss` baseline (`README.md`, `HANDOFF.md`, `CHANGELOG.md`, `verify.py`, `results/certificate.json`).
- Implemented deterministic SI `ACI 440.2R-17/-23` flexural equilibrium solver comparing unanchored EBR flat strips against NSM + $45^\circ$ truss-stitched T1100G CFRP retrofit kits (`+77.34%` moment gain at equal area; higher capacity at `50%` lower CFRP mass).
- Added $45^\circ$ shear-stitch resistance check (`eps_fv <= 0.004`) and 4-wire Cole EIS strain/moisture/debonding separability verification with 5 must-fail controls.
