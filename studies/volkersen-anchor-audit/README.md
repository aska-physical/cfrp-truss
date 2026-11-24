# Study: Exact-$\mathbb{Q}$ Discrete Volkersen Anchor Shear-Lag & Perforation-Tear Gate

- **Evidence Classification (`aska-physical/dock#8`):** **`MODEL` / `CHECKER`** (pure Python 3 standard library exact rational arithmetic; `0` floats; `0` bare `assert`s; not a physical bench measurement).
- **Pinned Upstream Reference:** `aska-physical/cfrp-truss @ 3c11631e223b71227531d10286abd71bc04d67b7` (`verify.py:278,282-283,369-371`; `results/certificate.json:82-108`).
- **Scope:** Strictly add-only companion study under `studies/volkersen-anchor-audit/` (`0` edits to `verify.py` or `results/certificate.json`).

## Motivation

In `verify.py:278,282-283`, the `12`-anchor (`6` longitudinal rows $\times$ `2` across width, uniform `220 mm` pitch, `L_zone = 1,100 mm`, `12 mm` drilled holes) end-prestress anchorage divides sustained and ultimate group tensions uniformly across all `12` dowels (`sus_1 = T_sus / n_anchors = 12.66 kN <= 16.14 kN` ACI 318-19 §17.5.2.2 epoxy creep cap).

Under elastic shear-lag compatibility between the CFRP chord ($EA_{\mathrm{CFRP}}$) and the concrete substrate ($EA_{\mathrm{sub}}$, stiffness ratio $r = EA_{\mathrm{CFRP}} / EA_{\mathrm{sub}} \ge 0$), discrete dowel-row cumulative loads $T_1, \dots, T_{m-1}$ satisfy the strictly diagonally dominant SPD tridiagonal Volkersen system:
$$-T_{i-1} + \big(2 + (1 + r)\lambda_i\big) T_i - T_{i+1} = \lambda_i W, \qquad \lambda_i = \alpha \frac{\Delta_i}{L_{\mathrm{zone}}}, \quad T_0 = 0,\; T_m = W.$$

## Certified Exact-$\mathbb{Q}$ Receiving-Side Results (`MODEL` / `CHECKER`)

1. **Uniform Load Sharing Underpredicts Lead-Row Tension:**
   - **Rigid-concrete substrate (`r = 0`, $\alpha = 1/4$):** Uniform `220 mm` spacing concentrates lead-row sustained load to `17.714 kN` ($\eta_{\mathrm{peak}} \approx 1.399\times$), **FAILING** the `16.14 kN` creep gate even though uniform division passes at `12.66 kN`. Front-dense graded spacing `[100, 150, 200, 250, 400] mm` over the exact same `1,100 mm` zone and `12` anchors cuts lead load to **`16.078 kN <= 16.14 kN`** (**PASS**, zero extra holes).
   - **Symmetric double-sheet lap (`r = 1`, $\alpha = 3/4$):** Uniform spacing peaks at `17.618 kN > 16.14 kN` (**FAIL**), while symmetric end-dense spacing `[110, 165, 550, 165, 110] mm` drops peak load to **`15.646 kN <= 16.14 kN`** (**PASS**).
   - **High shear-lag regime ($\alpha = 6$):** Uniform `6`-row spacing yields exact peaking factor $\eta_{\mathrm{peak}} = 349/153 \approx 2.281\times$ (`r = 1`) and `434171/111496 ≈ 3.894x` (`r = 0`).
2. **Perforation-Tear Interior Optimum $m^*$ & Must-Fail Control:**
   - Balancing lead-row pull-out against net-section ligament tear $T_0 (1 - m d_{\mathrm{eff}} / L_{\mathrm{zone}})$ yields a strict interior optimum $1 < m^* < m_{\max}$ for both `r = 0` and `r = 1`.
   - Over-perforating to `10` rows (`12 mm` holes in `1,100 mm` $\implies 89.09\% < 90\%$ ligament floor) is explicitly rejected by the receiving gate.

## Reproduce & Verify

```bash
cd studies/volkersen-anchor-audit
for flags in "-I -B" "-I -O -B" "-I -B -W error"; do
  python3 $flags volkersen_anchor_audit.py --check volkersen_anchor_certificate.json
  python3 $flags test_volkersen_anchor_r5.py
done
```
