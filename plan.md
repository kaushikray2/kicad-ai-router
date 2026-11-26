# Phase 2 plan — finisher upgrades

Status tracker for the Phase 2 programme. **This file is updated with
every commit that changes an item's status.** Estimates are part-time
hours, ±50%. Every workstream ships as its own release only if it wins
a same-code A/B on the standing evaluation set (the 7-board A/B set +
PCBWorld-Bench tiers + named fixtures).

Programme totals: hotfix 3–5 h; core path roughly 105–170 h; spikes
gated separately. Phase 2E (below) adds the WeaveEngine-derived
feature block, plus #63
riding as P2.1b's first step.

## Parallel lane — engineered nets (branch `engineered-nets`)

Created 2026-11-24 (owner's go) off main at c9e1223. The
engineered-nets block — P2.8, P2.9, P2.10, P2.11, P2.12 — plus
the placement audit preprocess (#42) is implemented on this
branch while main continues the finisher programme below. The
lanes run concurrently; neither waits for the other. Lane rules:

- The branch never reaches the production backends, their
  ledgers, or the self-test baselines; its runs use scratch
  backends with their own data dirs, and its A/Bs use the items'
  own fixtures — never the 49 held-out boards.
- **Merge gate:** the branch merges to main only after the final
  exam has run, or at a deliberate re-scope checkpoint that
  redefines the exam. Merging early voids the reason the branch
  exists.
- Rebase onto main after each finisher release; feature code
  stays additive (new modules, stages, report sections). A
  shared-code change a feature needs is proposed to main first
  as its own small item.
- One item in flight at a time, in this order: the persistence
  decision (P2.9 Step 0 — the owner's ruling; it blocks all
  marking), then P2.8 Step 1 (which builds the shared framework:
  marking, the stackup reader, the report), the P2.9
  schematic-extraction spike, then #42's materialisation layer
  (designed once for its three consumers: itself, P2.11, P2.12),
  then the remaining items and steps.

## Done

### Step 0 — v1.9.6 hotfix (3–5 h) — shipped 2026-11-23
Four verified defects in v1.9.5 fixed before Phase 2 measurement began:
the portfolio skip guard (passes were skipped on dirty-but-connected
winners), re-route window hygiene (stale stuck corridor; the target net
is now protected), copper_sliver counted in the fault key, and a hygiene
bundle. One finding — the deliberate faults-first / connectivity-first
key layering — was kept and documented in code and the CHANGELOG.

### P2.0 — Fault census (6–10 h) — done 2026-11-24
Decomposed 60 production run ledgers at three gates (base / routed /
final). Finding: the pipeline added **zero net copper faults** on the
bench sets — the 43 copper-only losses (416 faults) are placement
geometry in the input boards (solder_mask_bridge 221,
copper_edge_clearance 137, annular_width 56, drill_out_of_range 2).
Re-scored per board against its own base (CP_delta), the bench reads
easy 1.00, medium 1.00, hard 0.90. CP_delta is the headline metric from
here and the bench is Phase 2's regression guard. Consequences: P2.1b
re-aimed (below); P2.1's .kicad_dru item descoped (0 of 60 boards carry
one).

### P2.6-measure — engine escape modes (2–4 h) — done 2026-11-24, negative
`--escape-plan` and `--escape-flow` A/B'd on dense boards (bitaxe,
mikoto): escape-plan ties control on one board at +101 s and loses on
the other; escape-flow is a no-op. Neither earns a portfolio seat;
tm_escape stays a dormant opt-in.

### P2.1 — Exact checking (15–30 h) — shipped as v1.10.0, 2026-11-24 (issue #41)
`_new_items_clean` now tests keepouts exactly (half-width-dilated),
Edge.Cuts clearance, and drill hole / hole-to-hole clearance. Fixture
battery 9/9 (the old checker passed all four graze cases; the new one
rejects all four). A/B vs v1.9.6: reverted faults 11 → 7 across four
boards, gates equal on three, one +2-copper blemish on the 4-layer
self-test board within engine nondeterminism; wall-time ratios
0.98–1.29×.

### Off-plan — review hygiene batch, v1.10.1 + v1.10.2 (2026-11-24)
Ten findings from the fourteenth external review fixed (issues
#43–#60): backend config single-sourced from the plugin defaults,
TraceMaker install TOFU hardening, layer-law name resolution, lock-state
restore after a windowed re-route, engine process-group timeouts, a
pinned KRT binary, and the repo's first automated tests (tests/, five
files). v1.10.2 hotfixed a defect in v1.10.1's lock fix that its own
verification caught: the lock record was keyed by a SWIG proxy string
(identical for every item); it now uses `m_Uuid.AsString()`, with a
round-trip test that fails on 1.10.1. Verification (2026-11-24): all six
production self-tests PASS at unchanged baselines — hbridge and
mapone clean on both backends; the Nomad self-test at 0 unconnected /
0 splits / copper 14 on both backends, better than its {2, 2, 18}
baseline now that the portfolio actually runs on backend jobs. The
zorinbox backend container moved to a 16-CPU host the same day.

### Off-plan — layer-cost plumbing, v1.11.0 (2026-11-24)

Issue #73: graded layer pricing joins the binary ban/free layer
law. `layer_cost` (engine syntax, e.g. `In1.Cu=10,In2.Cu=10`) is
plumbed to every TraceMaker invocation and priced per cell in
the worker grids; a priced layer leaves the effective ban set,
an explicit spec disables layer-law auto escalation, and every
run records its effective layer policy in the ledger. Born of
the NAS three-way comparison, whose columns ran under unmatched
layer rules. The rerun of that column at In1.Cu=10,In2.Cu=10 on
this build is the feature's designated first benchmark — held
for the owner's separate authorization after the bug-review
pass.

### Off-plan -- layer-cost review fixes, v1.11.2 (2026-11-25)

A second external review cross-checked the v1.11.0 layer-cost
shipment claim by claim; verified against f8c2502 and fixed as
#101-#108 (gate double-summary, stitch layer-cost forwarding --
the v1.11.0 'vias only' rationale was wrong and is corrected on
#73, own-plane extra_layers pricing inversion, empty-ban-set
handling across pipeline and worker, worker CLI factor guards,
config-page layer_cost field, planner keepback parity with #75,
dead run_job loop). One finding was verified by-design and
dropped (run_auto's pre-resolution auto-escalation disable).
#109 (opt-in freerouting/krt candidates ignore layer_cost and
pay no gate penalty for priced-layer tracks) is filed and awaits
a design ruling. The NAS rerun remains held for the owner's
separate authorization.

### Off-plan -- portfolio layer_cost exclusion, v1.11.3 (2026-11-25)

#109 ruled (option 1) and fixed: while a valid layer_cost spec is
in force, tool_portfolio excludes freerouting and krt -- they
cannot honour the price, and priced layers carry no gate penalty
for them -- with each exclusion logged and recorded in the
portfolio ledger entry. Candidate sets are unchanged when
layer_cost is off. With this, every bug from the two external
reviews (#74-#109) is fixed; #80 closed the range on the
engineered-nets branch as net-selective Grid enforcement (it
carries no version of its own until the branch merges).

### Off-plan -- layer cost reaches the decision sites, v1.11.4 (2026-11-25)

#114 and #115 fixed: the v1.11.0 layer-cost feature priced every
search but no keep/discard decision consulted the price.
_accept_key and the orchestrator's _final_key now carry
cost-weighted wirelength as a lexicographic tail (tie-break only;
constant when uncosted, so uncosted runs are unchanged), the
worker gate census reports per-layer track length, and every gate
ledger record carries track_length_by_layer +
cost_weighted_wirelength_mm (null when uncosted) -- a layer-cost
run is now measurable from its own ledger. Basis: layer-cost
research report candidates 1-2 (2026-11-25).

### Third external review -- all main findings fixed, v1.11.5 (2026-11-25)

R1-R11 (#116-#126) fixed and closed with fail-before/pass-after
regression tests: CI workflow + pcbnew collection guards (#122),
strip keeps locked items and free vias with a gate invariance
census (#119), starved_thermal keyed as a base-relative delta
(#116), fractional layer-cost factors (#121), planner hole band
at the via pad radius (#117), curved Edge.Cuts tessellated in
planner and checker (#118), relative-first config imports with
reconciled fallbacks (#123), failed lock restore = failed attempt
(#124), _run_group Windows kill fallback (#125) plus the
subprocess NameError it exposed (#153, KRT/Freerouting legs
silently dead since v1.10.1), PCM v2 repository.json + rebuilt
1.11.5 package (#120), and release hygiene: backfilled tags
v1.10.0-v1.11.4, KRT binary hash pin, non-root container +
HEALTHCHECK, cached /health version (#126). On the
engineered-nets branch, the three merge blockers are fixed:
#132 (audit report-only by default), #133 (treatment key),
#150 (PCM glob + markings sidecar round-trip). Follow-ups:
the NAS layer-cost factor sweep (1.25/1.5/2/3) -- owner ruled
config (a) 2026-11-25 (In1.Cu = f, In2.Cu = 1 on the NAS board),
protocol pre-declared on #121, and the sweep is running on the
scratch backend (ML350:8733) on v1.11.6; #138's merge-time
remainder.

### Open-bug sweep (2026-11-25)

Every open issue inventoried and classified (report:
agent-runs/audit-verify/REPORT-SWEEP-2026-11-25.md). Main side:
the gate now REPORTS a via-on-pad census (#72 item 1 --
centre-in-pad split same-net vs foreign-net, plus
annulus-overlap; reported only, never in the accept key -- the
bounded policy work in #72 items 2-3 stays programme work);
the LLM config read is guarded against a non-numeric
max_tool_calls (#138 main-side item); #149's factual items
landed (D4/D5 README-vs-code corrections, D10 CHANGELOG
structure) and the README features/results sections now carry
the owner's supplied wording (D7/D8). Branch side: the
round-4 branch bugs #127/#128/#129/#130/#134-#138/#140/#142-
#148/#151/#152 fixed on engineered-nets (see the sweep
report); #144's proven validity-flag part landed, its
field-model stage remains open. Feature tickets verified
implemented were closed as such: #67-#71 + #42 (branch,
first-draft standard) and #73 (main). Addendum the same day:
#154 -- the PCM repository add failed in KiCad 10 because
repository.json's field shape diverged from the official
repository (extra schema_version/url/kicad_version fields, no
packages.update_time_utc); the generator and the served file
are aligned (owner re-add confirmation outstanding).

### Off-plan -- PCM discovery fix and repo tidy, v1.11.6 (2026-11-25)

#156: the PCM zip nested the plugin at plugins/ai_router/ with
no plugins/__init__.py, and KiCad's loader skips package
directories without one ('Skip subdir') -- PCM installs were
never discovered (owner hit this on Windows: package installed,
no toolbar button after a full restart, manual import OK).
build_package.py now writes a PCM entry point at
plugins/__init__.py importing the bundled ai_router package;
proven with KiCad's own pcbnew LoadPlugins on the extracted
install layout (1.11.5 layout skipped, 1.11.6 registers),
regression test tests/test_pcm_entrypoint.py. #157: the legacy
pcm/ folder and superseded pcm-repo/ zips (1.7.0-1.11.5) are
retired from the tree -- archived with a sha256 manifest
first; build_package.py builds the canonical zip into
pcm-repo/ (pcm/ remains, gitignored, as build output).
README carries the verified Windows install notes (Documents
3rdparty location, full-restart requirement, console
diagnostic, config path, the 1.11.5 scripting/plugins
workaround).

### Off-plan -- dialog redesign + result naming, v1.12.0 (2026-11-25)

#167: result boards are named <project>-<YYYYMMDD>-<HHMMSS>
(project = the sibling .kicad_pro stem, board stem as fallback;
generated suffixes stripped so names never compound -- the
routed-6040g-backend-backend failure; collisions take -2, -3,
never overwrite). Client-side in both modes: at the backend
download point, and as a post-run copy beside the source board
for local runs (the timestamped workdir stays as audit trail).
Server-side names unchanged.

#168: the plugin dialog is built from the open board instead of
free text. Per-layer ban checkboxes + cost preset dropdowns are
generated from the board's stackup (a layer name cannot be
mistyped; a cost spec cannot be malformed; pricing un-checks the
ban, the pipeline's own rule made structural). Smart defaults
at open: Auto mode and an Automatic layer law banning only
detected GND-plane layers; when the saved config differs on
mode/law, a one-line notice offers [Use recommended] instead of
silently inheriting a stale experiment (the mechanism behind
the owner's 59-unconnected Nomad run). A status lamp band
(colour + word + detail) reports IDLE (green) / RUNNING (red) /
DONE (green, with the final gate in the detail line -- green
never implies a clean board) / FAILED (orange-red) / CANCELLED
(grey), driven by the existing backend poll and local worker
lifecycle. Layer-law Automatic/Custom radio surfaces the
auto-escalation rule; backend URL gets presets, live validation
and a Test-connection button on the existing health() call;
the hidden config keys (finisher caps, engine portfolio,
pass_advice, layer_law_policy, and the LLM tool-call budget)
live in an Advanced collapsible, and max_tool_calls now rides
in the backend overrides so the dialog value governs backend
LLM runs (run_job.build_cfg already forwarded it). Existing
config.json files map losslessly (config keys and the strings
the pipeline consumes are unchanged). GUI smoke under xvfb on
the Nomad fixture asserts the recommended open state and the
stale-config notice path (21 checks, screenshots in
agent-runs/dialog-smoke/). Code only -- production backends
stay on 1.10.2, no redeploy.

### Off-plan -- SiliconBrane copyright headers (2026-11-25)

Template A of the SiliconBrane Copyright Header Standard
applied to every SiliconBrane-authored source file that lacked
one: the ai_router package, docker/server + run_job, the tests,
the bench/dataset scripts and the backend config page (HTML
comment form), each carrying its git creation year (2026 for
every file -- the repo history starts 2026-11-22). Pure comment
insertions (diff deletions: 0), no behaviour change, no version
bump; the shipped v1.12.0 PCM zip is unchanged -- the headers
ride the next release.

### Off-plan -- dialog display-fit fix, v1.12.1 (2026-11-25)

#169: the v1.12.0 dialog opened at a fixed 800x740 while its
content's natural size is ~950 px tall, so on 768-line displays
the bottom of the dialog (layers group tail, Advanced, status
lamp, Run/Cancel/Open buttons) was clipped with no way to reach
it. The settings stack now lives in a scrolled panel; the lamp
band, the action buttons and the log stay pinned outside the
scroll area; the opening size is the natural size capped at the
display's usable client area (95% x 93%, ui_model.capped_open_
size, unit-tested), and the dialog is explicitly resizable.
Layout/sizing only -- no control or behaviour changes. GUI
smoke at 1366x768 and 1024x768 under xvfb asserts the cap, the
pinned lamp/buttons and scroll reachability (screenshots in
agent-runs/dialog-smoke/). Code only -- production backends
stay on 1.10.2, no redeploy.

## In progress

- **P2.1b** is implemented on branch `p21b-fix-violations` and
  awaits the owner's review (status in its section below); the R4
  layer-cost sweep (#121) is running on the scratch backend.
  Phase 2E (below) follows P2.7a; the fair engine A/B sits inside
  it, per the owner's sequencing.

## Remaining (in sequence)

### P2.1b — tool_fix_violations (12–20 h)

**Status (2026-11-25): implemented on branch
`p21b-fix-violations` (tip 49ccc5c), NOT merged.** Step 0 (#63)
is closed; the tool (#155) is implemented and left open for the
owner's review. An external review of the branch was verified
claim by claim the same day: H1/H2/H3, M5, M6a and a
test-collection guard were proven real and fixed on the branch
as #158-#163 (closed, fail-before/pass-after); M4/M6b/M6c are
ticketed as design decisions #164/#165/#166, awaiting the
owner's ruling. What remains is measurement, not code: the
dataset-v3 bench rerun (step 0's improvement half) and a live
re-route-trade sample, both on the Phase 2E harness (#62) once
it exists. The merge awaits the owner's review.

**Step 0 — #63, pour skeleton reservation pre-check.** Compute each pour's pad spanning tree (width =
zone min-thickness + slack + clearance deltas, confined to the pour
region) before routing/repair and reserve it: severing the skeleton is
a first-class cost, the reservation is inserted last and yields first
under congestion. On the dataset-v3 grid, 22 of 47 failures were
pour-split-only; the skeleton is prevention, and the tool below is the
cure for what still gets through. The same skeleton defines, for the
diff below, which pour faults count as router-added. Exit for step 0:
split-only failures drop measurably on the bench, no completion or
copper regression.

Repair copper faults on already-connected nets: diff the board's DRC
violations against the base board's; for each router-added fault, rip
only the offending net's copper within ±3 mm and re-close with the
exact-checked finisher under the faults-first accept key. Re-aimed by
the census: no bench upside (engine copper on connected bench boards
does not exist); the targets are re-route trades (bitaxe: +13 copper
bought −4 unconnected) and dense-board engine output. Also runs after
`reroute_window`, whose trades become worth taking if the added copper
can be cleaned locally. Exit: copper strictly down, unconnected
unchanged, on the sample. Kill: rip-and-reclose re-opens more than it
cleans.

### P2.7a — Performance quick wins (4–8 h)
Remove verified double-gating (the base is gated twice per run; the
final is gated by finalize and again by both callers; the portfolio
winner is re-gated — ~2 gate cycles saved per run, ~4 on escalation);
a persistent worker process or batched gate calls; profile one AUTO run
end to end and attack the top fixed cost.

### Phase 2E — engine & measurement block
Six features adapted from the WeaveEngine review (2026-11-24; ideas
reimplemented, no code taken). The block sits here — after P2.7a,
before P2.7b — because its verdicts multiply: the harness and the
deterministic portfolio are preconditions for a fair engine A/B, and
that A/B's battery is where #64/#65 are judged. #63 is not here; it
rides as P2.1b's step 0 (above), because P2.1b's pour-side fault
definition comes from it.

1. **#62 — bench harness: prepared-input caching + nothing-worse
   gate.** Cache prepared inputs keyed by a hash of the
   source board files (re-prep on unchanged sources is byte-stable);
   score "worse" on routing-caused DRC — per board, per violation
   kind, DRC after routing minus DRC of the stripped input — never
   on raw counts. Committing the harness this way also discharges
   #54's remainder. First because every later verdict in this block,
   and the final exam, rides on it.
2. **#66 — portfolio racing, fixed-order selection.** Race
   whole engine passes at varied parameter sets, in heats; selection
   keeps the first COMPLETE variant in a fixed priority order, never
   the first to finish — the chosen result is independent of worker
   count and finish order, preserving the no-deadline determinism
   contract. Discharges #52's reproducibility defect in the portfolio
   stage. Before the A/B, so the A/B measures engines rather than a
   timing race.
3. **Fair engine A/B — #45, #46, #52.** The queued slot, now here:
   KRT design-invariance gate + `--keep-placement`; Freerouting
   `-mt 1` and power-type DSN layer export; tm_relax bounded by
   rounds, on #66's deterministic selection. Report faults /
   wirelength / vias alongside unconnected.
4. **#64 — severance penalty in finisher candidate scoring**
   and **#65 — regret-order sequencing for multi-net repair.** Implemented against the finisher and judged
   on this block's A/B battery, per their acceptance criteria: keep
   each only if the A/B says it earns its cost.
5. **#61 — best-so-far output + shared time-limit semantics.** Atomic artifact writes; a checkpoint hook rewrites
   the output whenever a pass beats the best-so-far acceptance key;
   one wall-clock deadline shared by every worker in a backend run,
   after which the run finishes with the best so far. Results
   produced under a deadline are labelled non-deterministic.
   Independent of the A/B; placed last in the block, and
   pull-forwardable if a lost long run hurts first.

### P2.7b — C/C++ A* kernel (6–10 h)
A small ctypes 3D grid A* so the P2.2 / P2.3 searches are not
interpreter-capped (expected 50–100× on the inner loop). Identical
paths out for identical grids in, proven against the Python
implementation on the fixture battery before use.

### P2.2 — MCR victim selection, top-K (15–25 h)
`_plan_attempts` emits a single cut-set when singles are empty (attempt
selection engaged on only 2 of 10 battery nets in v1.7.0 for this
reason). Generate top-K (K=3..5) distinct cut-sets, price victims by
probed re-bridge cost, and add the free-space impossibility pre-check
so impossible attempts are rejected in seconds. Exit: re-bridge success
measurably above the v1.6.0 zero-closure baseline. Kill: probed costs
do not predict re-bridge success on the battery.

### P2.3 — Windowed PathFinder, half-halo edition (35–55 h)
Rip up all nets in a window, negotiated congestion with history,
exterior copper locked — with usage and history accumulated on the
Minkowski half-halo around each trace and via (two legal traces share
zero centreline cells while violating clearance; cell overlap is not
the conflict relation), bystander traces clipped at the window boundary
into fixed boundary pins, and iteration on the P2.7b kernel.
**Re-scope checkpoint before starting:** re-read this plan against
whatever the TraceMaker improvement programme has shipped by then.
Exit: closes ≥2 named residuals the current finisher cannot, at copper
≤ best-known, no regression on the 7-board set. Kill: two tuning
iterations with no closure the finisher + P2.2 cannot match → park it
with the measurements kept.

### P2.4 — 45° staircase collapse (4–8 h)
Collapse multi-step A* staircases into 2-segment 0°/45°/90° doglegs
under the P2.1 exact checker. Exit: equal closures, lower copper and
wirelength on the finish battery.

### P2.8 — Impedance-controlled nets (issue #67)
A feature axis, not finisher repair: user-marked nets get
stackup-computed widths, priority routing, EMC treatment, and
gate-verified arc smoothing. It sits here — after P2.4, whose
exact-check discipline its smoothing post-pass reuses, and after the
Phase 2E harness (#62) that its nothing-worse verification runs on.
It displaces nothing in the sequence above; it pulls forward only if
a funded work package takes it (the NLnet application names it),
decided at a re-scope checkpoint. Three steps, in the issue's own
staging:

**Paired with P2.9.** P2.8 and P2.9 are one framework — engineered
nets: shared marking, permanent first_nets, locking, and the per-net
verification report. P2.8 is the impedance sizing model; P2.9
(next) is the power sizing model. One framework decision precedes
both: where marks live (sidecar vs project file) — #68 names it an
open item, and it supersedes the "plugin config" wording in Step 1
below, which stands until the decision is taken.

- **Step 1 — marking, width, priority, report.** Marked nets and
  their target impedance live in the plugin config; an RF marking
  also carries the net's operating frequency. Marking a net RF
  switches transmission-line treatment always ON — there is no
  short-feed exemption (lambda/20 is 3.4 mm at 2.4 GHz; every real
  feed is electrically long). Frequency scales severity only:
  pitches, stub limits, the warning set. Width per layer
  from the board stackup via the IPC-2141 / Wadell closed forms (the
  equations KiCad's PCB Calculator uses), every assumption printed,
  placeholder stackups warned on, the computed width written into
  the net's class before routing. Marked nets route first — a
  permanent user-declared first_nets set — and are protected from
  later re-route windows. The run report states, per marked net:
  target vs calculated width, layer(s) used, via count,
  reference-plane check, and
  for RF nets the three scaling quantities: lambda_g at f,
  W/lambda_g, and estimated loss per unit length at f. The report
  states plainly that the tool verifies geometry and stackup math;
  it does not predict S11.
  Verification follows the annex's VER-1..5: the run generates fab
  notes (coupon request, Dk basis, finish/mask assumptions) with
  the coupon-is-not-the-feed disclaimer, and a pre-fab checklist
  gate mirroring vendor layout reviews; kicad-rfsim is a future EM
  bridge, explicitly not P2.8 scope. Exit: computed widths match KiCad PCB
  Calculator within rounding on a defined-stackup fixture; a marked
  net routes first at the computed width; unmarked nets do not
  regress on the standing A/B set.
- **Step 2 — EMC treatment + arc smoothing.** Keep marked nets on
  a layer with a solid reference; a gate check that the routed path
  crosses no split or gap in its reference pour — in the annex's
  form, reference-corridor severance within ±3h of the path is
  the strongest gate in the set (GATE-1..12); a GND return via
  beside every layer-change via on a marked net, within 2.54 mm,
  tightening to 1.5 mm in the 3–6 GHz band (all pitches in
  in-dielectric wavelength). Via fencing's job is GCPW ground
  tying only, at banded pitch (lambda_d/20 sub-GHz and 1–3 GHz,
  lambda_d/10 default at 3–6 GHz) — there
  is no measured evidence it improves an open microstrip feed's
  S11, and the tool does not claim it does; no guard traces are
  auto-inserted, and floating copper alongside marked RF nets is
  gated instead (a guard counts only if stitched at both ends). Vendor-derived feed-structure gates apply at all
  bands: vias in the feed gated where avoidable; branches and
  stubs gated at lambda_g/20; pi-match presence and the 50-ohm
  segment between the radio and antenna matches gated (GATE-7;
  displacement from the correct end is WARN-6) — superseding the
  refinements comment's presence-only wording; antenna keep-out
  intrusion checked;
  no thermal-relief spokes on RF-adjacent ground pads.
  Frequency-scaled warnings: feed loss computed and warned
  against budget (WARN-1..8: warn at 0.3 dB, GNSS/LNA at 0.2 dB,
  0.5 dB default budget; FR-4 ~0.1 dB/cm at 2.4 GHz rising to
  ~0.2 dB/cm at 5–6 GHz); ENIG on the RF path flagged above
  1 GHz, gate-visible at 3–6 GHz or on feeds over 20 mm (never
  a fail, never a precise number); copper roughness warn-only. Arc smoothing is a post-pass on marked
  nets only (fitted arcs, radius ≥ 3× trace width), accepted
  per net only if the exact gate proves zero clearance
  regression, rolled back otherwise — the engine stays
  straight-segment. Generation follows the annex's GEN-1..8
  (arcs R ≥ 3W or double-45, constant finished width, no feed vias
  by default, manufacturer land patterns treated as RF geometry).
  Bends are generation, not gate theatre: the gate checks only
  acute angles and width variation along the net (square corners
  are not a gate failure), and the tool never prints bend
  return-loss numbers (no
  calibrating measurement exists). Where a mitre is generated
  instead of an arc, it uses the Douville & James optimum,
  M = 52 + 65 · e^(−1.35 · W/h) percent. Explicitly NOT encoded
  (the research rules them out as over-treatment; annex
  NO-1..12): back-drilling
  at <= 6 GHz, bend return-loss quantification,
  roughness-corrected impedance (Huray needs fab foil parameters
  we will not have). Severity follows the comment's banded table
  (sub-GHz / 1–3 GHz / 3–6 GHz); at mmWave the tool refuses to
  present its checks as validation. Exit: each treatment gate-verified clean on the
  battery (zero added faults). Kill: a treatment that cannot place
  without gate regressions ships as a report-only warning instead —
  the gap-crossing check already is one.
- **Step 3 — fast follow: differential pairs.** Width + gap for
  edge-coupled microstrip / stripline (e.g. 90 Ω USB). Length
  matching is explicitly out: a separate later item — length does
  not set impedance.

**Implementation sketch** (design discussion, 2026-11-24). Design
reference: issue #67 itself, its "Research refinements
(2026-11-24)" comment, and above all the ticket's design-rules
annex (two comments on #67, 2026-11-24) — the normative rule
set for this item, cited below by rule family: CALC-1..6,
GEN-1..8, GATE-1..12, WARN-1..8, NO-1..12, VER-1..5. Where the
annex differs in detail from the refinements comment, the annex
wins. Annex part 1 (CALC / GEN / GATE):
https://github.com/kaushikray2/kicad-ai-router/issues/67#issuecomment-7144947253
— part 2 (WARN / NO / VER, vendor quantified values, the banded
table, sources, open questions):
https://github.com/kaushikray2/kicad-ai-router/issues/67#issuecomment-7144971963
The plugin
encodes the math and the design rules as code; it never embeds the
standard's text.

- **Calculator module** (new, pure Python, unit-tested): the
  IPC-2141A closed-form family — surface and embedded microstrip,
  symmetric and asymmetric stripline, coplanar waveguide,
  edge-coupled differential — each giving Z0 from width, inverted
  by bisection on width. Every model carries its published validity
  range (e.g. microstrip ±5% only for 0.1 < W/H < 2.0, 1 < er < 15);
  outside range the tool reports it and falls back rather than
  extrapolating silently. Two corrections are modelled (the annex's CALC-1..6 also fix
  band evaluation — thresholds at band top, loss at band centre
  — and keep any roughness term out of the calculator): Dk at the
  operating frequency, using the fab's design Dk where published
  (the wrong Dk choice alone is a ~2.5% systematic Z0 error); and
  a solder-mask correction that switches on when dielectric height
  h <= ~0.25 mm (modelled 50 -> 48.7 ohm at h = 0.21 mm) and off at
  thick dielectrics. Fixtures: Saturn PCB Toolkit worked
  examples and KiCad PCB Calculator outputs (Step 1's exit).
- **Stackup reader:** BOARD_STACKUP via pcbnew (dielectric
  thickness and er per layer pair, copper thickness). Each copper
  layer is classified — surface microstrip, stripline between two
  planes, coplanar when a GND pour shares the layer — and the
  classification selects the formula. Placeholder stackups (default
  er everywhere, missing thicknesses) are flagged in the run report;
  the calculation is only as good as that data.
- **Run integration:** the computed width (and gap for pairs) is
  written into the working copy's netclass before routing; marked
  nets join the permanent first_nets advice set (the advice channel
  caps at 64 first nets, TraceMaker's limit); marked-net copper is
  locked against re-route windows via a net-scoped lock pass on the
  v1.10.1 lock machinery — today that machinery is corridor-scoped
  (it locks by geometry around a target net), so the net-scoped
  variant is an extension of the hook, not an existing switch.
- **Gate checks are the report:** post-route, per marked net —
  actual routed width measured, achieved Z0 recomputed from the
  actual width and stackup against target ± tolerance, the
  reference-pour gap-crossing check, via count. Return vias and via
  fencing are placed by the finisher through the existing stitch
  via-placement machinery; arc smoothing runs last, per net, under
  the exact checker.
- **Sources and licensing (hard rule):** implement the republished
  equations and cite IPC-2141A as the source — never lift IPC's
  verbatim text or tables into the repo. IPC-2141A is a paid
  standard (US$178, ANSI Webstore) and current IPC editions
  prohibit transferring their content into AI systems; the
  archive.org copies are unofficial uploads and are not a source.
  The equations are legitimately free: Saturn PCB Toolkit
  (cross-check reference), the open-source pcb-toolkit crate source
  on docs.rs (the 2141A closed forms and validity ranges),
  Wevolver's controlled-impedance guide, and National
  Semiconductor AN-905 (via TI) for the differential-microstrip
  forms. Designation correction: the RF/microwave guide is
  IPC-2252, not IPC-2251 (2251 is high-speed packaging).

### P2.9 — Power nets: worst-case current sizing (issue #68)
The second sizing model of the engineered-nets framework (paired
with P2.8, above): user-marked power nets sized by worst-case
circuit analysis instead of netclass defaults — per segment,
width = max(IPC-2152 thermal width, voltage-drop width) at stacked
corners. Sequenced after P2.8 because it builds on the shared
framework (marking, first_nets, locking, report); its sizing core
is independent of P2.8's impedance math. The ticket is the
reference document; this entry is its plan shape.

- **Step 0 — persistence decision (framework-level, before
  build).** Where marks, currents, mode tags, and corner settings
  live — sidecar vs project file — decides whether they travel
  when a project is shared. One answer for both P2.8 and P2.9.
- **Spike — schematic extraction (before Step 1).** Step 1's
  tree presupposes an input nothing in the stack has ever read:
  a schematic. This time-boxed probe proves or breaks that
  input on real schematics (the Nomad's, plus two or three from
  the open-schematics corpus) before the WCCA core is built —
  or estimated — on top of it. Three questions: (1) does
  kicad-cli's netlist export carry per-pin electrical types
  (they live in the symbol definitions), or must the .kicad_sch
  be parsed directly — either route is acceptable, the spike
  establishes which is real; (2) does the tree skeleton
  (sources / loads / conduits per power net, hierarchical
  sheets instance-counted) reconstruct correctly, hand-checked
  end to end on one real board; (3) what breaks — power
  symbols (a rail's root must resolve to the regulator or
  connector feeding it, or be recorded honestly as an external
  input), pins typed passive/unspecified in real libraries,
  multi-unit parts. Exit: extraction proven on the test set
  with one hand-verified tree, and Step 1 proceeds as written.
  Redirect: if pin types prove too sparse in practice, node
  types become user-declared in the marking step (the authority
  order already puts user values first) and Step 1 is
  re-scoped on that basis — the spike's output is the verdict,
  not code we keep.
  **Verdict (2026-11-24): PROVEN.** The netlist export carries
  pin types + sheet paths (no .kicad_sch parsing needed);
  instance counting proven on KiCad's multichannel demo; the
  Nomad tree hand-verified end to end. Loads classify cleanly
  (38/38 power_in pins are IC supply pins). The gap is sources:
  only 2 power_out pins on the Nomad (its LDO and load switch) —
  rails are fed by passive-typed connectors, the charger's VBAT
  is a plain output, and power symbols leave no trace in the
  export. Two rules join Step 1's model: output-typed pins on
  power nets count as candidate sources, and a rail still
  sourceless after that gets its root declared at marking time.
  The full user-declared fallback is NOT needed — it narrows to
  rail roots. Results filed as a comment on #68; full report
  internal (agent-runs/nets-spike/REPORT.md).
- **Step 1 — power-tree WCCA core** (pure, unit-tested). The
  tree is built from the schematic's pin electrical types
  (power_out = source, power_in = load, the rest conduits),
  hierarchical sheets instance-counted — a new input surface:
  the pipeline today consumes boards only, so the schematic
  netlist (kicad-cli sch export) joins the run inputs. A segment's
  design current is the sum of its downstream load maxima
  (Extreme Value Analysis; RSS is explicitly wrong for
  simultaneous rail loads), with operating-mode/duty tags so
  mutually exclusive branches are not summed. Node types: loads
  (resistive / constant-current / constant-power; each carries a
  peak current for drop and a sustained current for thermal),
  conduits (rating CHECK only — a conduit's rating never sizes
  a trace; the downstream load sum does), protective devices
  (their rating is a FLOOR for the segments they guard, so the
  device stays the weakest link in a fault), regulators as
  transformer nodes (buck: input power = output power / eta_min,
  input current at minimum input voltage; LDO: Iin = Iout + Iq).
  Sizing: IPC-2152 thermal (Saturn PCB Toolkit as the fixture
  reference; the IPC-2221 closed form kept as a cross-check only)
  and drop V = I · rho(T) · L / (W · t) over the full current
  loop, rho at maximum operating temperature; corners are minimum
  manufactured cross-section, maximum ambient plus the user's
  delta-T budget, and the user's drop budget. Exit: sizing math
  matches Saturn outputs on the fixture set within rounding, and
  per-segment design currents match a hand-worked WCCA table
  exactly on the fixture schematic (one buck, one LDO, one
  FET-switched branch with mutually exclusive loads, one fused
  input, one hierarchical repeated load).
- **Step 2 — run integration + verification.** Per-segment
  widths applied to the routed board; via arrays placed by the
  finisher (barrel cross-section matching, IPC-2152; via
  violations reported per segment). One friction to design
  around: the pipeline sets width per net through the netclass,
  while the tree sizes per segment — where a tree segment is
  its own net they coincide; the rest needs a widen-only post-pass
  on finished copper under the exact checker. Drop is computed on
  the full loop (a return trace's or fragmented pour's resistance
  included; a solid-plane return is stated as an assumption);
  pour-carried segments are reported as such (interplay with the
  #63 skeleton). Post-route, every segment is recomputed with
  actual lengths and widths; the report states design currents
  with each load's contribution and its source, the corner set,
  thermal and drop results per segment, via counts and
  violations, and every assumption. The report also carries a
  decoupling checklist per load — cap present on the rail local
  to the load, pin-to-cap distance against the stated budget,
  via-path sharing note — a geometric checklist, not a PDN
  simulation (rider comment on #68, 2026-11-24). A segment that cannot meet
  its budget in the available space is reported failed with its
  shortfall — never silently widened into clearance
  violations. Fallback: with no currents available, a
  resistances-only report (loop resistance per rail pair, no
  sizing claim) — the tool never invents a current. Exit: the
  ticket's acceptance on the fixture board; unmarked nets do not
  regress on the standing A/B set.
- **Step 3 — KB estimation layer (separate service, ships
  last, blocks nothing).** Authority order: user-entered values,
  then KB values the user has confirmed, then nothing —
  unconfirmed estimates are never applied to sizing. The KB is a
  separate service behind a small versioned interface (the
  kb-mcp shape: query by MPN + condition → structured values
  with citation and confirmed flag; write-back for
  confirmations). Extraction returns value + type (peak/
  sustained) + conditions + citation (datasheet document and
  revision, table, page); the user confirms once per part in a
  review queue; confirmed values cache under MPN + datasheet
  revision (MPN alone is an incorrect key). Deployment modes:
  hosted central corpus / optional bundled compose profile /
  bring-your-own endpoint. Setup lives on the autorouter
  webpage (endpoint status, datasheet upload, confirmation
  queue, corpus view — not LibreChat); hosted deployments pin
  the endpoint — custom endpoints are self-hosted-only (SSRF).
  This narrows, and does not revive, the ditched KB-to-router
  idea: the KB supplies structured electrical values to a
  deterministic calculator, never layout prose or routing
  advice. Exit: every estimated current in a report traces to a
  citation the user confirmed or a value the user typed.
  Deferral: this step can slip without blocking Steps 1–2 —
  user-entered currents are the authority and need no KB.
- **Stated limitations** (from the ticket): thermal coupling
  between adjacent power conductors is not modelled; drop
  verification is DC only (no pulsed-load droop, no decoupling
  dynamics).

### P2.10 — Length-matched groups (issue #69)
The third sizing model of the engineered-nets block: delay.
Sequenced after P2.8 — it reuses that item's per-layer delay
math (effective permittivity from the stackup) and its
post-pass pattern. The user declares a match group (member nets
or one differential pair), a target (longest member / absolute
length / absolute delay) and a tolerance in mm or ps. Generation
is a post-pass on finished nets: meander / trombone length added
in available space, per member, accepted only if the exact
checker proves zero clearance regression and connectivity is
unchanged, rolled back otherwise; marked and locked copper is
respected. The report states per member: routed length, computed
delay, delta to target, in/out of tolerance. Exit: on a fixture
pair with a known delta, skew lands within tolerance at zero
added gate faults and unmarked nets do not regress; reported
lengths/delays match hand measurement within rounding; a member
that cannot fit reports its shortfall — never silently
accepted. Stated limitation: no field solver, no meander
self-coupling model; the report says so.

### P2.11 — High-voltage domains (issue #70)
The safety axis of the engineered-nets block. The user assigns
nets to voltage domains (working voltage plus the assumptions
the tables need: pollution degree, material group / CTI, coating
or altitude where applicable — all printed in the report).
Required creepage and clearance are computed from the published
standard tables (IPC-2221B / IEC 62368-1 family) as code, under
#67's licensing discipline. Gate checks on the routed board:
clearance in air between domains, and creepage as a surface
path — measured around slots, grooves and board edges, not as
a straight-line distance; under-component paths only where
footprint body data exists, otherwise reported as unmodelled.
Pre-route option: materialise domain clearances as keepouts the
engines respect (interplay with #42). The tool reports
violations with the limiting geometry located; it does not move
copper to fix them. Exit: table outputs match published worked
examples within rounding; on a fixture with a slot and known
spacings, measured creepage/clearance match hand computation
and a planted violation is caught and located. Stated
limitation: a design aid, not a certification.

### P2.12 — Quiet nets (issue #71)
The GATE-family extension of the engineered-nets block — no new
math engine, so it is the cheapest item here and may pull
forward ahead of P2.10/P2.11 without disturbing them. The user
marks nets (or a component's pins, e.g. a crystal's) as quiet,
with an isolation distance; a small profile set (crystal,
analog, reference) supplies stated, overridable defaults.
Post-route gates: no foreign tracks or vias inside the quiet
corridor except the net's own connections; no aggressor
crossings over/under on adjacent layers within the corridor;
crystal land-pattern keepout respected; load-cap ground-return
continuity; guard continuity under #67's termination rule.
Optional pre-route keepout materialisation (interplay with
#42). The report lists intrusions with locations, per quiet
net. Exit: a planted intrusion on the fixture is caught and
located; a clean fixture passes; with materialisation on, the
engines produce no copper inside the generated keepout and
unmarked nets do not regress. Stated limitation: geometric
isolation only — no coupling simulation, no dB claims.

### P2.5 — Push-and-shove spike (15–20 h, hard stop, gated behind P2.3)
Runs only if P2.3 leaves a shove-shaped residual class behind. Default
verdict NO unless it produces a closure nothing else has.

### Final exam
The 49 held-out dataset-v3 boards have never been run. At the end of
Phase 2: run once, Phase 2 stack vs v1.9.6 stack, both numbers reported
whatever they say. Do not tune against them. The exam runs on the
Phase 2E harness (#62): cached prepared inputs, scored by the
routing-caused-DRC nothing-worse gate alongside CP_delta.

## Queued outside Phase 2

- **Fair engine A/B** (issues #45, #46, #52): moved into Phase 2E
  (above).
- **Issue #54 remainder:** commit the PCBWorld-Bench harness —
  discharged by Phase 2E item #62 (above).
- **Placement audit preprocess** (issue #42): moved into the
  engineered-nets branch lane (see Parallel lane, above).
- **Engine adoption:** the plugin runs the pinned TraceMaker release.
  Trying newer engine builds is parked until the TraceMaker programme's
  own Phase 2 finishes.

## After Phase 2 — Phase 3 gets planned from the results

There is deliberately no Phase 3 plan yet. Phase 2 was scoped to end in
measurements, and Phase 3 will be written from them. Planning it
earlier would mean sizing workstreams against numbers nobody has.

### The evidence Phase 3 will be planned from

- **The final exam.** The 49 held-out boards, Phase 2 stack vs v1.9.6
  stack: clean pass / CP_delta, unconnected, copper, wall time. This is
  the generalization number — it says whether Phase 2's wins hold on
  boards nothing was tuned against, and where the remaining losses
  live.
- **Per-item verdicts.** Each workstream's A/B outcome, including the
  kills. If P2.3 parks, its measurements bound what negotiation can buy
  here; if P2.2's probed costs predict re-bridge success, victim pricing
  becomes a platform later items can build on — and if they do not,
  that line closes.
- **The re-scope checkpoint record.** What the TraceMaker programme
  shipped during Phase 2 (its later stages: a determinism mode, a
  finish mode, bystander-safe re-route) and what the engine-adoption
  measurement then showed. Engine capability decides how much router
  the plugin still needs to be.
- **The fair engine A/B.** Whether KRT and Freerouting, run fairly,
  earn permanent portfolio seats changes the portfolio's shape — and
  what a per-instance chooser would have to choose between.
- **The residual census.** A P2.0-style decomposition run on the final
  stack: whatever fault classes and unconnected residuals remain after
  Phase 2 are Phase 3's problem statement, in the same base / routed /
  final terms.

### Candidate directions (uncommitted)

- **KiCad IPC migration.** KiCad 11 removes the SWIG bindings the
  worker is built on; moving board access to the IPC API is the one
  candidate with an external clock. Its size depends on how much of
  the worker's pcbnew surface Phase 2 leaves standing.
- **Engine integration, second round.** If the engine's finish mode
  and deterministic mode land and measure well, finisher stages retire
  or re-shape around them, and self-test tolerances can shrink toward
  exact gates.
- **Per-instance algorithm selection.** The portfolio already logs
  per-board features and per-engine outcomes; at this data scale the
  research conclusion was a SATzilla-style chooser over learned
  policies. Phase 2's batteries roughly double the labelled runs
  available to build one from.
- **Placement audit preprocess (issue #42).** Materialise library
  rules on the working copy and report placement-shaped faults before
  routing — the census showed placement geometry is the dominant fault
  source, and no router release fixes it. (Since moved into the
  engineered-nets branch lane — Phase 3 inherits its results
  rather than planning it fresh.)

### How it gets written

Same discipline as Phase 2: drafted from the evidence above, reviewed
against the code before adoption, sequenced measurement-first, and
every workstream carries an exit and a kill criterion. Until then,
this section is the whole of the Phase 3 plan.
