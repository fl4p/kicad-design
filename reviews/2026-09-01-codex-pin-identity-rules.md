# Codex xhigh adversarial review — pin-identity and reviewer-contract rules

Date: 2026-09-01  ·  Reviewer: independent Codex agent, model_reasoning_effort=xhigh
Target: uncommitted diff to FOOTPRINTS.md + SETUP.md (115 insertions)
Prompt: /tmp/skillrev-prompt.txt (7 numbered attack targets)

Sources it verified: DS-0637 Issue 1 (db3c996d) and Issue 2 (4d1d4479); Diodes
DS30086 Rev 31-2 (39c16a68); KiCad 10.0.5 local manual (a87371d8).
NOTE: the Playwright/CDP browser did NOT attach ('browser runtime exposed no
binding'); it worked from local primaries only and no finding depends on a fetch.

FINDINGS:

1. **(c) [FOOTPRINTS.md:24](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:24), [SETUP.md:316](/Users/fab/dev/ee/kicad-design/SETUP.md:316) — “Never from prose” is over-broad.** Diodes Incorporated’s common 1N4148W SOD123 datasheet, DS30086 Rev. 31-2, page 1, has no numbered terminal-function table; it establishes physical identity through the prose “Polarity: Cathode Band” plus the marking drawing. The literal rule makes that pin map impossible to establish. **Fix:** prohibit deriving order from an *unordered enumeration of signal names*. Permit any explicit terminal relation—numbered table, labelled drawing, polarity/marking statement tied to a drawn feature, package standard, or validated measurement—and record the relation used.

2. **(a) [FOOTPRINTS.md:37](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:37) — the visible-pin bottom-view heuristic is false.** The same 1N4148W page explicitly labels a solid SOD123 package with both metal terminals visible as “Top View.” Its marking drawing puts the cathode band at the left; misclassifying that as bottom view and mirroring it moves the cathode to the right. Side elevations—including the SGX figure itself—are another immediate counterexample. **Fix:** accept an explicit TOP/BOTTOM/TERMINAL-SIDE label or reconcile multiple orthographic views against a named datum. If the view remains inferred, record the inference and fail `UNVERIFIED`; never infer it merely from terminal visibility. Express any mirror as a coordinate transform about a named axis, not universally as “left-to-right reverses.”

3. **(c) [FOOTPRINTS.md:53](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:53) — the three-pad threshold misses load-bearing two-pad orientation.** A 1N4148W switching or clamp diode has only two pads, but swapping anode and cathode changes or defeats its function. LEDs, photodiodes, unidirectional TVSs, batteries, and polarized capacitors have the same failure class. **Fix:** trigger the check whenever terminals are not electrically interchangeable, independent of pad count; explicitly include polarized two-terminal parts.

4. **(c) [FOOTPRINTS.md:43](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:43) — the token check is a false-confidence mute button.** `pinmap:` presently contains a document locator, not the actual map; `table:` contains only a citation; and every value is free text. A C-R-W footprint can pass verbatim with `pinmap: DS-0637…`, `view: BOTTOM`, and a confidently wrong `leaders:` string. This contradicts the existing guard contract that a check observe the shipped artefact and be calibrated with a known-bad mutation. The assertion that the fields “cannot be written down without doing the work” is self-certification. **Fix:** define a parseable schema containing the post-transform mapping, for example `pad1=Counter; pad2=Working; pad3=Reference`, source revision/hash, view, datum and transform. Compare it with the saved footprint’s actual pad numbers/coordinates and the symbol pin functions, then calibrate it against every three-pin permutation plus top/bottom legal cases. Token presence alone must never pass.

5. **(c) [SETUP.md:323](/Users/fab/dev/ee/kicad-design/SETUP.md:323) — “the drawing is the sole authority—do not fall through to hardware” contradicts the new footprint rules.** [FOOTPRINTS.md:61](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:61) permits electrical identification, while [FOOTPRINTS.md:66](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:66) declares any single-source map unverified. A sole drawing cannot simultaneously require corroboration, and some devices need an explicit marking statement to interpret the drawing. **Fix:** say the drawing must be inspected and cannot be replaced by a claim that hardware is necessary; then separately require corroboration by another document or measurement. Keep the normative rule canonical in FOOTPRINTS.md and make SETUP.md point to it.

6. **(a) [SETUP.md:336](/Users/fab/dev/ee/kicad-design/SETUP.md:336) — “a review that cannot fetch cannot verify” is false.** A reviewer can verify local primary PDFs, schematics, ERC, DRC, netlists and renders without network access. It also conflicts with [SKILL.md:62](/Users/fab/dev/ee/kicad-design/SKILL.md:62), which requires the browser gate only when external search or fetching is required. **Fix:** require a browser only for review claims needing external retrieval or current web state; require validated local access for local-source reviews.

7. **(c) [SETUP.md:342](/Users/fab/dev/ee/kicad-design/SETUP.md:342) — “every datasheet” and unconditional archival are not an executable evidence contract.** The controlling primary may instead be a customer drawing, package standard, erratum, CAD model or measured identification; some datasheets also cannot be durably archived without an authorized location or compatible retention terms. **Fix:** require a claim-scoped evidence manifest listing each required primary work, revision and hash. Archive only under the project’s authorized retention policy; otherwise retain a stable pointer and hash/provenance record.

8. **(c) [SETUP.md:347](/Users/fab/dev/ee/kicad-design/SETUP.md:347) — a reviewer may silently evade “Fail closed” by redefining scope.** Declaring a requested component “out of scope” because its source is missing weakens [SETUP.md:403](/Users/fab/dev/ee/kicad-design/SETUP.md:403), which requires missing load-bearing evidence to block the conclusion. **Fix:** encode `IN_SCOPE + SOURCE_MISSING => UNVERIFIED/BLOCKED`. Only the task specification or explicit user authorization may remove a component from scope.

9. **(c) [SETUP.md:354](/Users/fab/dev/ee/kicad-design/SETUP.md:354) — voiding every finding “on that component” destroys valid findings and has no usable boundary.** A DRC clearance violation at U1, a short involving its pads, or a disconnected bypass capacitor remains a valid artefact fact without U1’s datasheet. Mixed findings such as “U1 lacks required decoupling” cross component, net and requirement boundaries. **Fix:** classify findings by evidence dependency. Missing primary evidence invalidates source-dependent conclusions and any blanket “verified/no findings” statement; preserve artefact-only findings and state their narrower scope.

10. **(c) [FOOTPRINTS.md:63](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:63) — the rotation sentence is imprecisely generalized.** For a five-pin inline array, 180° rotation swaps 1↔5 and 2↔4, not merely the outermost pins; only pad 3 remains fixed. The centre-survival result assumes a collinear, uniform, 180°-symmetric pattern and in-plane mounting. Even-count, two-row and non-collinear parts require their own symmetry analysis. **Fix:** state: “For an odd, uniform inline pattern, 180° rotation maps position `i` to `N+1−i` and leaves the centre position fixed; therefore a wrong centre assignment cannot be corrected by an allowed in-plane rotation.”

11. **(c) [FOOTPRINTS.md:68](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:68), [SETUP.md:368](/Users/fab/dev/ee/kicad-design/SETUP.md:368) — status propagation is currently cosmetic.** No naming grammar, saved-artifact checker, BOM/export rule or release assertion is defined. Appending “unverified” to a library name can remove a warning without proving that existing schematic/board instances or fabrication outputs carry it. **Fix:** define one machine-readable `PinmapStatus` representation, verify it in the saved footprint, board instance, schematic assignment and release manifest/BOM, and calibrate release refusal with a deliberately unverified fixture.

SURVIVED:

- **The measured SGX map is C-W-R.** Both [DS-0637 Issue 1](</Users/fab/dev/ha/farming/compostlab-literature/datasheets/SGX-OX-ROHS-Mini oxygen sensor datasheet DS-0637 Issue 1.pdf>) and [Issue 2](</Users/fab/dev/ha/farming/compostlab-literature/datasheets/SGX-OX-ROHS-Mini oxygen sensor datasheet DS-0637 Issue 2.pdf>), PDF page 2, trace Counter to the left pin, Working to the centre pin, and Reference to the right pin. The adjacent side elevation establishes that the lower plan drawing is the underside; the rejected heuristic is not needed.

- **Render drawing pages.** Both SGX text layers omit the electrode callouts entirely. `pdftotext` cannot establish their spatial relations.

- **Trace every leader individually; do not use label order, proximity, or nearest-label.** The SGX figure directly validates this rule.

- **KiCad F.Cu is represented from the top/front side.** KiCad 10.0.5’s installed manual states that all PCB layers are viewed from the front and bottom-side footprints therefore appear mirrored: [getting_started_in_kicad.html:1602](/Applications/KiCad/KiCad.app/Contents/SharedSupport/help/en/getting_started_in_kicad.html:1602).

- **Geometry correctness and pin-identity correctness are independent.**

- **The actual three-pin centre-error conclusion survives.** Rotating C-W-R by 180° produces R-W-C; Working remains in the centre, so the built C-R-W board cannot be rescued by rotation.

- **The high-level requirements to carry provenance with the footprint, corroborate a pin map, gate an uncorroborated map, and keep deferrals release-visible survive.** Their proposed machine representations need the fixes above.

- **The existing instruction to cross-check merged/notched land pairing against the numbered pin-function table remains sound.**

Evidence access: both SGX revisions were inspected at the rendered Product Dimensions figure and identity/footer, using raw text plus rendered-page vision and SHA-256 `db3c996d…e6d9` / `4d1d4479…ae7`; Diodes DS30086 Rev. 31-2 page 1 was similarly rendered and validated, SHA-256 `39c16a68…3fc08`; KiCad 10.0.5’s local HTML was inspected at its version block and front-view paragraph, SHA-256 `a87371d8…93f8d`. All are load-bearing primary sources. The advertised browser/CDP surface was unverified because the browser runtime exposed no binding; no finding depends on an external fetch.
