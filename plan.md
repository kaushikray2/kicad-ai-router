# Phase 2 plan — finisher upgrades

Status tracker for the Phase 2 programme. **This file is updated with
every commit that changes an item's status.** Estimates are part-time
hours, ±50%. Every workstream ships as its own release only if it wins
a same-code A/B on the standing evaluation set (the 7-board A/B set +
PCBWorld-Bench tiers + named fixtures).

Programme totals: hotfix 3–5 h; core path roughly 105–170 h; spikes
gated separately.

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
round-trip test that fails on 1.10.1.

## In progress

- v1.10.2 release verification: production self-tests running on both
  backends (verdict pending, 2026-11-24).
- Next up: **P2.1b**.

## Remaining (in sequence)

### P2.1b — tool_fix_violations (12–20 h)
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

### P2.5 — Push-and-shove spike (15–20 h, hard stop, gated behind P2.3)
Runs only if P2.3 leaves a shove-shaped residual class behind. Default
verdict NO unless it produces a closure nothing else has.

### Final exam
The 49 held-out dataset-v3 boards have never been run. At the end of
Phase 2: run once, Phase 2 stack vs v1.9.6 stack, both numbers reported
whatever they say. Do not tune against them.

## Queued outside Phase 2

- **Fair engine A/B** (issues #45, #46, #52): KRT design-invariance gate
  + `--keep-placement`; Freerouting `-mt 1` and power-type DSN layer
  export; tm_relax bounded by rounds; report faults / wirelength / vias
  alongside unconnected.
- **Issue #54 remainder:** commit the PCBWorld-Bench harness.
- **Placement audit preprocess** (issue #42): future feature, not in
  Phase 2 scope.
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
  source, and no router release fixes it.

### How it gets written

Same discipline as Phase 2: drafted from the evidence above, reviewed
against the code before adoption, sequenced measurement-first, and
every workstream carries an exit and a kill criterion. Until then,
this section is the whole of the Phase 3 plan.
