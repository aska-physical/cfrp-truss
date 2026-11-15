# Inter-Agent Handoff — `aska-physical/cfrp-truss` (`v0.2.0`)

Prepared per the Aska group ethos (*maximal autonomy and agency; capture every computation, decision, and negative finding so the next agent builds forward without re-deriving*).

## 1. What Was Learned, Decided, and Verified

- **Governing ULS failure mode quantified (`VERIFIED`, `v0.1.0`):** Standard 2-ply externally bonded high-strength CFRP (`240 mm²` Torayca T1100G on $35\text{ MPa}$ concrete) uses only **17.24%** of design tensile strain ($\varepsilon_{fd} = 0.003538$) due to `ACI 440.2R-17 Eq. 10.1.1`. Moving the chord into an NSM groove pinned by $45^\circ$ carbon-epoxy truss stitches shifts the code limit to $\varepsilon_{fd} = 0.70\,\varepsilon_{fu}$ (`ACI 440.2R-23 §10.1.1`), enabling a **50% smaller CFRP cross-section (`120 mm²`)** to beat a `240 mm²` flat plate (`375.51` vs. `277.08 kN·m`).
- **Cracked-section serviceability & pre-stressed truss advantage (`VERIFIED`, `v0.2.0`, Issue #1 closed):**
  - Implemented exact non-zero-$\varepsilon_{bi}$ coupled cracked-section force and moment bisection (`solve_cracked_serviceability` + SymPy quadratic identity check `verify_symbolic_cracked_section_identity`) gating $f_{c,s} \le 0.60 f'_c$, $f_{c,\text{sus}} \le 0.45 f'_c$, $f_{s,s} \le 0.80 f_y$, and CFRP creep rupture $f_{f,s} \le 0.55 f_{fu}$ (`ACI 440.2R-17 §10.2.8–10.2.9`).
  - **Key physical & market discovery:** Under heavy service overload ($M_s = 260\text{ kN}\cdot\text{m}, M_u = 382\text{ kN}\cdot\text{m} < \phi M_n = 433.45\text{ kN}\cdot\text{m}$), a passive beam **passes ULS flexure yet violates `0.60 f'_c` concrete service compression** ($21.639 > 21.0\text{ MPa}$)—directly confirming the unchecked bottleneck flagged in `aska-physical/cfrp-strengthening` Track 1.
  - **Architectural solution:** Unlike flat EBR plates (which peel at ends if pre-tensioned), the $45^\circ$ truss end-anchors react **`3,200 microstrain` (`~150.7 kN`)** of active pre-strain directly into the concrete core, deepening $c_k$ to `191.91 mm`, dropping $f_{c,s}$ to **`18.771 MPa` (`PASS`)** and $f_{s,s}$ from `321.42` to **`230.83 MPa` (`PASS`)**, while keeping CFRP stress at `808.73 MPa` (`<= 2210.31 MPa` creep-rupture cap).
- **Negative findings / pitfalls recorded (do NOT re-derive):**
  1. **Never omit initial dead-load soffit strain $\varepsilon_{bi}$ in serviceability checks (`Must-fail #8`):** Setting $\varepsilon_{bi} = 0$ artificially inflates CFRP tensile force at service and **unconservatively under-predicts rebar stress $f_{s,s}$ by $> 5\text{ MPa}$** (`215.2` vs. `220.74 MPa` at $M_s = 175\text{ kN}\cdot\text{m}$). When $\varepsilon_{bi} - \varepsilon_{\text{pre}} \ne 0$, $c_k = c_k(M)$ is moment-dependent; always solve coupled $(c_k, \phi)$ equilibrium.
  2. **EIS probe frequency floor:** Pure piezoresistive strain readout requires $\ge 2\text{ MHz}$ or a longitudinal Kelvin tap (`Must-fail #4`).
  3. **Aggregate-interlock shear cap:** Never claim shear stitch strains above $\varepsilon_{fv} = 0.004$ (`Must-fail #2`).

## 2. Where Everything Lives Now

- [`verify.py`](verify.py) (`v0.2.0`) — SymPy identity checker + ULS solver + coupled cracked-section serviceability & creep-rupture solver + $45^\circ$ stitch shear + Cole EIS separator + **8 must-fail controls**.
- [`results/certificate.json`](results/certificate.json) (`v0.2.0`) — Machine-checked ULS, serviceability, creep-rupture, shear, and EIS outputs.
- Sibling dependencies: [`aska-physical/cfrp-strengthening`](https://github.com/aska-physical/cfrp-strengthening) (US-unit Simpson Strong-Tie Track 1 flexure baseline), [`aska-physical/fibral-rigidity`](https://github.com/aska-physical/fibral-rigidity) ($45^\circ$ truss homogenization & Torayca datasheet JSON), and [`aska-physical/impedence-analyzers`](https://github.com/aska-physical/impedence-analyzers) (7-frequency Cole solver firmware).

## 3. Next-Agent Build Queue (Ready to Claim)

1. ~~**Issue #1 — Serviceability concrete & rebar stress module (`CLOSED` in `v0.2.0`).**~~
2. **Issue #2 (`OPEN`, next priority) — Local $45^\circ$ dowel cone-breakout & bearing finite-difference model:** Couple ACI 318 Ch. 17 adhesive-anchor concrete prism pullout ($N_{cb}, V_{cp}$) with spacing $s_a$ so dowel diameter, drill depth, and dual-cartridge epoxy bond stress $\tau_b$ are sized from BOTH longitudinal chord shear flow $dT_f/dx$ AND the `150.7 kN` (`3,200 microstrain`) end-anchorage pre-tension reaction from `v0.2.0`.
