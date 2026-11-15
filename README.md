# cfrp-truss

**Truss-Anchored, Self-Sensing CFRP Concrete & Column Retrofit Kits (`v0.3.0`).**

Combines the ACI 440.2R-17/-23 beam mechanics of [`aska-physical/cfrp-strengthening`](https://github.com/aska-physical/cfrp-strengthening), the $45^\circ$ tension-tie truss mechanics of [`aska-physical/fibral-rigidity`](https://github.com/aska-physical/fibral-rigidity), and the multi-frequency Cole dispersion model of [`aska-physical/impedence-analyzers`](https://github.com/aska-physical/impedence-analyzers) (`Foundation-0/nhanes-bioimpedance`).

## Why Existing Market Products Fall Short

Externally bonded (EBR) flat carbon plates and wet-layup fabrics (such as [Sika CarboDur](https://tha.sika.com/dms/getdocument.get/168c00c1-7092-31fc-9dea-a9a18fc4a9de/User%20Guide%20Sika%20Carbodur%20ACI440,English_Version%202.0.pdf) and [Simpson Strong-Tie FRP](https://www.foundationperformance.org/pastpresentations/BohrenPres-Slides-11Oct17.pdf)) are governed by intermediate-crack (IC) peel debonding under [ACI 440.2R-17 Eq. 10.1.1](https://fidstrong.com/ar/blog-slab-and-beam-strengthening-fabric-plate-or-nsm/):

$$\varepsilon_{fd,\text{EBR}} = 0.41 \sqrt{\frac{f'_c}{n E_f t_f}} \le 0.9\,\varepsilon_{fu}.$$

On a standard $300 \times 600\text{ mm}$ RC beam ($f'_c = 35\text{ MPa}$) retrofitted with 2 plies ($240\text{ mm}^2$) of unidirectional [Torayca T1100G](https://www.toraycma.com/wp-content/uploads/T1100G-Data-Sheet.pdf) CFRP ($E_f = 195.8\text{ GPa}, \varepsilon_{fu} = 2.05\%$ after $C_E = 0.95$), `verify.py` proves that IC debonding caps $\varepsilon_{fd}$ at **$0.003538$ ($17.24\%$ of fiber capacity)**—stranding $82.76\%$ of the carbon fiber.

## Verified Product Architecture & Market Superiority

1. **NSM + $45^\circ$ Truss-Stitched Mechanical Anchorage:** Routing pultruded T1100G longitudinal chords into near-surface grooves locked by $45^\circ$ dual-cartridge-epoxy carbon shear-stitch dowels (anchored into the stirrup core) suppresses cover-peel debonding (`ACI 440.2R-23 §10.1.1` NSM regime $\varepsilon_{fd} = 0.70\,\varepsilon_{fu}$, [FDOT Anchorage Report](https://fdotwww.blob.core.windows.net/sitefinity/docs/default-source/research/reports/fdot-bea90-rpt.pdf?sfvrsn=a5e367be_8)):
   - **50% less carbon fiber, higher strength:** A `120 mm²` truss-anchored kit achieves **$\phi M_n = 375.51\text{ kN}\cdot\text{m}$**, beating a `240 mm²` flat EBR plate (**$277.08\text{ kN}\cdot\text{m}$**) while cutting carbon mass in half.
   - **+77.34% higher flexural gain at equal fiber area (`240 mm²`):** Reaches **$\phi M_n = 433.45\text{ kN}\cdot\text{m}$** ($43.24\%$ fiber utilization, governed by concrete crushing ductility rather than premature peel).
   - **Simultaneous $+177.2\text{ kN}$ shear strengthening:** The $45^\circ$ stitches add $V_f = 177.22\text{ kN}$ at the strict `ACI 440.2R §11.4.1.2` aggregate-interlock cap ($\varepsilon_{fv} \le 0.004$).
2. **Cracked-Section Serviceability & Pre-Stressed Truss Relief (`ACI 440.2R-17 §10.2.8–10.2.9`, Issue #1):**
   - Solves exact coupled cracked-section equilibrium accounting for initial dead-load soffit strain $\varepsilon_{bi} = 0.00047$ ($M_{DL} = 55\text{ kN}\cdot\text{m}, c_{k,0} = 160.75\text{ mm}$) across four code gates: total concrete stress $f_{c,s} \le 0.60 f'_c$ ($21.0\text{ MPa}$), sustained concrete stress $f_{c,\text{sus}} \le 0.45 f'_c$ ($15.75\text{ MPa}$), steel stress $f_{s,s} \le 0.80 f_y$ ($336.0\text{ MPa}$), and CFRP creep-rupture stress $f_{f,s} \le 0.55 f_{fu}$ ($2210.31\text{ MPa}$).
   - **Design service envelope ($M_s = 175\text{ kN}\cdot\text{m}, M_{\text{sus}} = 91\text{ kN}\cdot\text{m}$):** Passive `240 mm²` kit passes all four gates ($f_{c,s} = 14.70\text{ MPa}, f_{c,\text{sus}} = 7.83\text{ MPa}, f_{s,s} = 220.74\text{ MPa}, f_{f,s} = 167.10\text{ MPa}$).
    - **Heavy service overload ($M_s = 260\text{ kN}\cdot\text{m}, M_{\text{sus}} = 165\text{ kN}\cdot\text{m}, M_u = 382\text{ kN}\cdot\text{m}$):** Reproduces the upstream `cfrp-strengthening` Track 1 bottleneck—a passive kit passes ULS ($382 < 433.45\text{ kN}\cdot\text{m}$) yet **fails concrete serviceability** ($f_{c,s} = 21.64 > 21.0\text{ MPa}$). Locking **$3,200\,\mu\varepsilon$ active pre-strain** ($\sim 150.7\text{ kN}$) into the $45^\circ$ truss end-anchors deepens $c_k$ from $169.65$ to $191.91\text{ mm}$, cutting $f_{c,s}$ to **$18.77\text{ MPa}$**, $f_{c,\text{sus}}$ to **$10.96\text{ MPa}$**, and rebar stress by $-90.59\text{ MPa}$ to **$230.83\text{ MPa}$**, while holding CFRP stress at **$808.73\text{ MPa}$** ($36.6\%$ of the $2210.31\text{ MPa}$ creep-rupture limit).
3. **Coupled Finite-Difference Shear-Flow, `ACI 318M-19 Ch. 17` Dowel Sizing & Dual-Cartridge Epoxy Package (`ICC-ES ESR-3814`, Issue #2):**
   - **Post-yield shear-flow spike (`3.70x` over average):** Finite-difference equilibrium proves $dT_f/dx$ jumps **`5.36x`** once steel yields (`79.00` $\to$ **`423.14 N/mm`**), driving unassisted groove stress to `4.07 MPa` (`> 2.66 MPa` concrete peel cap); pairs of `8 mm` $45^\circ$ stitches at $150\text{ mm}$ (`L_drill = 226 mm`) safely carry the `21.94 kN` excess (`22.16 <= 34.18 kN`, `interaction = 0.518`).
   - **Inverse dowel-diameter theorem & stirrup-core end lock:** SymPy proves $\partial N_{ag}/\partial d_a = -N_{ag}/d_a < 0$ when beam width $b < 2 c_{Na}$ truncates projected bond area—so `12 x 8 mm` $45^\circ$ dowels (`L_drill = 230 mm`, engaging closed stirrups per `§17.5.2.1`, $\phi_s N_{\text{stirrups}} = 445.1\text{ kN}$) deliver **$\phi N_{ag} = 195.76\text{ kN}$ (`PASS` vs. `182.28 kN` ULS & `12.66 <= 16.14 kN` `§17.5.2.2` sustained creep)**, whereas oversized `16 mm` dowels drop to `110.23 kN` (`FAIL`) and `90°` vertical pins fail in guillotine shear.
   - **Exact field epoxy count:** `2,288.4 mL` gross mixed epoxy (`1,545.6 mL` grooves + `692.8 mL` `12 mm` boreholes + `50 mL` static-mixer purge) = **`4 x 600 mL` side-by-side dual cartridges per `6.0 m` beam (`95.4%` utilization)**.
   4. **Zero-Sensor-Wire Self-Sensing Bondline QA:** Driving a 4-wire Cole impedance sweep ([EIS Composite Monitoring](https://www.researchgate.net/publication/321206389_Impedance_spectroscopy_as_a_tool_for_moisture_uptake_monitoring_in_construction_composites_during_service)) directly through the conductive carbon chord separates axial load strain (`+0.409%` $2\text{ MHz}$ resistance shift, `0.00%` $50\text{ kHz}$ reactance crosstalk), sub-$T_g$ epoxy moisture ingress (`22.4%` $50\text{ kHz}$ reactance shift), and incipient debonding (`+111.6%` $100\text{ Hz}$ contact-resistance jump).

## Reproduce & Verify

```bash
python3 verify.py
```

Checks all ACI 440.2R section-equilibrium equations, $45^\circ$ shear-stitch capacities, Cole EIS separability metrics, and *12 must-fail controls** (SymPy symbolic cracked-section identity + ULS + `§10.2.8` serviceability + `§10.2.9` creep-rupture checks) against [`results/certificate.json`](results/certificate.json).

## `aska-physical` Ecosystem & Upstream Lineage

| Repository | Role in Physical Stack | Upstream `Foundation-0` Lineage |
| :--- | :--- | :--- |
| [`aska-physical/fibral-rigidity`](https://github.com/aska-physical/fibral-rigidity) | Continuum CFRP truss homogenization, area-grading (`alpha_K* = 2*sqrt(2)`, `alpha_E* = 2*sqrt(6)/3`), and 45-deg pore-tie theory | [`Foundation-0/incentre-lattices`](https://github.com/Foundation-0/incentre-lattices) |
| [`aska-physical/cfrp-strengthening`](https://github.com/aska-physical/cfrp-strengthening) | Step-by-step re-runnable ACI 440.2R concrete beam strengthening baseline | [`Foundation-0/cfrp-strengthening`](https://github.com/Foundation-0/cfrp-strengthening) |
| [`aska-physical/cfrp-truss`](https://github.com/aska-physical/cfrp-truss) | Truss-anchored NSM + 45-deg shear-stitched, self-sensing CFRP beam/column retrofit kits | `cfrp-strengthening` + `fibral-rigidity` + `nhanes-bioimpedance` |
| [`aska-physical/cfrp-masts`](https://github.com/aska-physical/cfrp-masts) | Snap-locked 3.32x-compacting deployable CFRP booms, telecom towers, and solar masts | `incentre-lattices` + `fibral-rigidity` + [`Foundation-0/rigid-realizations`](https://github.com/Foundation-0/rigid-realizations) |
| [`aska-physical/venturi-collars`](https://github.com/aska-physical/venturi-collars) | Battery-free aeroelastic M55J/PCB-coil Venturi collars for VAV HVAC & industrial ducts | [`Foundation-0/ductricity`](https://github.com/Foundation-0/ductricity) + [`Foundation-0/thermal-capture`](https://github.com/Foundation-0/thermal-capture) |
| [`aska-physical/impedence-analyzers`](https://github.com/aska-physical/impedence-analyzers) | Artifact-rejecting 7-frequency Cole EIS analyzers for structural NDT & clinical BIA | [`Foundation-0/nhanes-bioimpedance`](https://github.com/Foundation-0/nhanes-bioimpedance) + [`Foundation-0/point-charges`](https://github.com/Foundation-0/point-charges) |

## Attribution & Ethos

- **Architect & Human Sign-Off:** Shahid Arifuddin (`@abu-aska`)
- **Foundational `f0` Lineage:** Ahraz Arifuddin (`@ahrazzle`)
- **Autonomous Agent Capture:** Google Gemini (per Aska inter-agent collaboration ethos; see [`HANDOFF.md`](HANDOFF.md) and [`CHANGELOG.md`](CHANGELOG.md)).
