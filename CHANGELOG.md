# Changelog -- development record

This file is the project's engineering log, kept version by version as the
work was measured. It is candid about dead ends (LLM geometry routing, ILP
candidate selection, negotiated congestion) because the negative results are
the reason the shipped design looks the way it does. References to "the
in-house board" are to a 4-layer board developed alongside the router; its
design files are not part of this repository. Corpus boards (PCBench,
open-schematics) are named as published by their authors.

Entries below are in a single descending order, newest first (the pre-version dated records from 2026-11-21 close the file).  The README-style sections that used to sit here (Install / Use / Modes / Honest limits) moved to the README appendix on 2026-11-25 (#149 D10).

## License change -- 2026-11-24

The project is relicensed from MIT to **GPL-3.0-or-later** (SPDX: GPL-3.0-or-later), matching KiCad's own licensing. The change applies from this commit forward; releases through v1.10.2 were distributed under MIT, and copies already out there keep those terms. Sample boards in `docker/selftest/` remain under their original licenses.

## v1.11.5 -- 2026-11-25

Third external review: all eleven main findings (R1-R11) fixed,
plus one found during the work (#153). Every fix carries a
regression test that fails on v1.11.4 and passes here.

- #122: CI -- a GitHub Actions workflow builds the backend image
  and runs pytest in it, and a packaging job verifies the PCM zip
  contents, the packages.json version and both repo JSON files
  against the PCM v2 schema. The four pcbnew test files gained
  importorskip guards so the suite collects without pcbnew.
- #119: strip no longer deletes user-locked routing or free vias:
  it removes only unlocked tracks/arcs and vias left dangling by
  that removal (cmd_prune honours locks too), and the final gate
  records an invariance census of locked items and free vias
  between the input and the delivered board.
- #116: starved_thermal is a keyed acceptance tier (as the
  routing-caused delta against the base board, after faults);
  isolated_copper is reported in the gate but not keyed.
- #121: layer-cost factors may be fractional (up to two decimal
  places, 1..1000) in the pipeline parser, the worker parser and
  the dialog; integer specs produce byte-identical engine command
  lines, malformed specs still warn and disable, never raise.
- #117: the planner's via hole-clearance band uses the via PAD
  radius, matching the checker (the drill radius remains for the
  hole-to-hole band); the parity test probes a cell between the
  two radii.
- #118: curved Edge.Cuts are no longer approximated by bounding
  boxes in the planner or the checker -- shapes are tessellated
  by KiCad (TransformShapeToPolygon, 0.05 mm max error,
  ERROR_OUTSIDE) and measured as real segments.
- #123: config imports are relative-first in pipeline and
  orchestrator; the hard-coded fallbacks agree with config.py
  (reroute_accept 'connectivity', engine_portfolio 'tm_relax');
  run_job logs a failed config import instead of passing
  silently; the package __init__ prints the ImportError it
  swallows.
- #124: a failed lock restore after a windowed re-route is a
  failed attempt (recorded 'lock_restore_failed', re-route
  discarded) instead of a warning on the way to acceptance.
- #125: the _run_group timeout kill has a Windows branch
  (taskkill /T on the process-group child, p.kill() as last
  resort) instead of silently degrading to an unbounded
  communicate(); POSIX behaviour unchanged.
- #153: _run_group never had subprocess in scope, so the KRT and
  Freerouting portfolio legs silently returned run_failed on
  every call since v1.10.1 -- fixed by importing subprocess in
  _run_group/_kill_group (found and proven during the #125 work).
- #120: build_package.py emits a PCM v2 repository.json
  (schema_version 2 + a packages resource with the sha256 of the
  packages.json written in the same build); this release's
  package is rebuilt at 1.11.5, and the README install section
  and version badge are corrected/synced.
- #126: release hygiene -- annotated git tags backfilled for
  v1.10.0..v1.11.4 at their release commits; the KRT grid_router
  binary is sha256-pinned in the Dockerfile (digest verified
  against the GitHub API and a cached copy); the backend
  container runs as a non-root user with a HEALTHCHECK; /health
  serves a startup-cached kicad-cli version.
- Branch (not in this package): the engineered-nets merge
  blockers were fixed on the engineered-nets branch in the same
  programme -- #132 (placement audit report-only by default,
  gate-checked AIR:-named zone materialisation), #133
  (treatment key, merge-safe against the layer-cost tail),
  #150 (PCM glob packaging, loud markings-import failure,
  backend markings sidecar round-trip).

## v1.11.4 -- 2026-11-25

- #114: layer cost reached every search site in v1.11.0 but no
  decision site: _accept_key compared only (faults, unconnected,
  splits), so a finisher leg could displace priced-layer copper
  and be kept whenever the gate numbers tied (NAS layer-cost run:
  engine passes placed In1.Cu segments, the delivered board had
  none). The worker gate census now reports per-layer track
  length, tool_gate derives cost-weighted wirelength from the
  run's resolved spec, and _accept_key carries it as its last
  term -- tie-break only, never above faults/unconnected/splits.
  With no layer_cost configured the weighted figure is absent and
  the term is a constant, so uncosted acceptance is unchanged.
  Regression: tests/test_accept_cost_tail.py (fails on v1.11.3,
  passes here).
- #115: pass/variant selection was cost-blind the same way, and
  the run ledger recorded the layer_policy but nothing it did.
  The orchestrator's _final_key gains the same cost tail (its
  (unconnected, splits, faults) order is unchanged) and the pass
  loop's two inline copies of that key now call it; every gate
  record carries track_length_by_layer and
  cost_weighted_wirelength_mm (null when no layer_cost is in
  force), so a layer-cost run is measurable from its ledger
  alone. Regression: tests/test_selection_cost_ledger.py (fails
  on v1.11.3, passes here).

## v1.11.3 -- 2026-11-25

- #109: the portfolio silently mis-scored opt-in freerouting/krt
  candidates under layer_cost. Those engines take no layer-law
  arguments, and priced layers leave the effective ban set, so the
  gate levied no penalty on their priced-layer tracks -- they
  competed at 1x against TraceMaker's priced cost. While a valid
  layer_cost spec is in force, tool_portfolio now excludes them,
  logs each exclusion with the reason, and records it in the
  portfolio ledger entry. With layer_cost off (or invalid, hence
  off) the candidate set is unchanged. Regression:
  tests/test_portfolio_layer_cost.py (fails on v1.11.2, passes here).

## v1.11.2 -- 2026-11-25

Fixes from the cross-check of the layer-cost review against v1.11.1
(f8c2502). Every claim was verified at code level against the tip
before filing; every fix carries a fail-before/pass-after regression
test:

- #101: tool_gate paid for two `summary --fast` worker runs per step
  board (its own census fetch plus a layer-info cache miss). The
  gate's summary now seeds the layer-info cache.
- #102: tool_stitch never received --layer-cost. The v1.11.0
  exclusion rationale ('stitch places vias only') was wrong --
  _bridge_island draws track bridges on the Grid. Stitch forwards
  the price like every other leg; the correction is recorded on #73.
- #103: a net's own-plane layers passed via extra_layers were charged
  the layer_cost factor in astar/cutset_astar, so pricing a layer
  made its own nets' bridges dearer than under the hard ban
  (monotonicity inversion). Vouched layers travel at unit cost.
- #104: an explicitly empty ban set re-banned In1.Cu in worker Grids
  ('' was treated as unset). Legs now pass the board-resolved
  effective ban set, and the worker parses '' as an explicit empty
  set, skipping empty tokens (a trailing comma crashed Grid before).
- #105: the worker --layer-cost CLI accepted factors the config
  parser rejects (0, negative, >1000); both parsers now enforce the
  same guards (non-empty name, digits only, 1..1000).
- #106: the backend config page had no layer_cost field; wired
  render/load/save like no_tracks_on (the server already persisted
  the key).
- #107: planner keepback stamps gained #75 parity -- footprint
  Edge.Cuts in the edge stamp, and same-net drills in the hole
  stamp's hole_to_hole via band (track bands stay foreign-only).
- #108: removed the dead v1.8.2 config-forwarding loop in
  run_job.main() -- dead since #74 moved forwarding into build_cfg.
- #109: FILED, awaiting a design ruling -- opt-in freerouting/krt
  portfolio candidates ignore layer_cost and pay no gate penalty
  for priced-layer tracks. Not fixed here.
- One review finding was verified BY DESIGN and dropped: run_auto
  disabling layer-law auto escalation when layer_cost is set,
  before resolving layer names. The behaviour is asserted by
  test_layer_cost.py and matches the explicit-no_tracks_on
  precedent in orchestrator.run_auto. No ticket, no change.

## v1.11.1 -- 2026-11-24

Fixes from the 2026-11-24 external audit, each verified against the
current tip before filing (probes / fail-before regression tests):

- #74: backend jobs silently dropped overrides for DEFAULTS keys not
  hardcoded in run_job.build_cfg (reroute_window_cap, reroute_accept,
  reroute_margin, pass_advice, advice_regions, max_tool_calls ran
  with shipped defaults). build_cfg now forwards the remaining keys;
  server-owned keys stay unoverridable.
- #75: _new_items_clean never tested same-net hole_to_hole pairs and
  ignored Edge.Cuts drawn inside footprints, so heal/finish items
  passed the checker and faulted at the DRC gate. Both collected now.
- #76: ai_router/__init__.py imported the KiCad plugin
  unconditionally, making every pure-Python module unimportable
  outside KiCad's Python. Import/registration is ImportError-guarded.

## v1.11.0 -- 2026-11-24

Layer-cost plumbing (issue #73): layers can now be PRICED
instead of only banned or free. New config key `layer_cost` in
the engine's own syntax (`In1.Cu=10,In2.Cu=10`; factor an
integer 1..1000, 1 = normal cost). Motivation: the 2026-11-24
NAS three-way comparison ran its columns under unmatched layer
rules -- the bare engines used all four copper layers freely
while the plugin banned both inner layers -- and the shipped
stack had no supported way to express the owner's ruling
(inner layers allowed, at high cost). TraceMaker already spoke
`--layer-cost`; nothing in the plugin reached it.

- Pipeline: strict spec parsing (any malformed token rejects
  the whole spec with a log warning; treated as off, never
  raises), name resolution against the board's alias map, and
  the conflict rule -- a priced layer is NOT banned: it leaves
  the effective `no_tracks_on` set, and the gate's
  banned-layer census keys off that effective set. Every run
  records its effective layer policy in the ledger
  ('layer_policy' entry). `--layer-cost` rides on every engine
  command line (route passes, portfolio variants, windowed
  re-route) and into the worker legs that route.
- Worker: the finish/long/ilp/heal grids keep priced layers
  routable and charge the factor per cell in both A* searches
  (the unit-cost heuristic stays admissible; factors are >= 1);
  a layer sent as both banned and priced is priced. stitch is
  unchanged -- it places vias and draws no tracks.
- An explicit `layer_cost` disables `layer_law_policy` 'auto'
  escalation, the explicit-`no_tracks_on` precedent.
- Dialog: a 'Layer costs' field beside 'No tracks on layers';
  backend routing whitelist + run_job carry the key into
  backend jobs, and plugin-submitted jobs include it in their
  overrides.
- tests/test_layer_cost.py covers parsing, resolution,
  command-line construction (flag present on every engine
  invocation; byte-identical lines when unset), the conflict
  rule, the gate census, the auto disable, backend build_cfg
  and worker Grid pricing. Suite 6/6 files pass.

The feature's first benchmark -- the NAS plugin column rerun
at In1.Cu=10,In2.Cu=10 on this build, measured identically to
the original three-way -- is queued behind the owner's
bug-review pass and will be reported with the comparison.

## v1.10.2 -- 2026-11-24

Hotfix for a defect in v1.10.1's #50 fix, found by the fix's own
verification an hour after the release: cmd_lock_window keyed its
lock-state record by str(m_Uuid), which in KiCad 10 is the SWIG
proxy repr -- the SAME string for every item -- so the record
collapsed to a couple of keys and cmd_restore_locks restored
nothing (changed: 0 on a 163-track board; 78 tracks would have
kept the window pass's locks). Both sites now use
m_Uuid.AsString(). The user-lock protection half of #50 (never
unlock user-locked copper in the window) was and is correct.

- tests/test_lock_roundtrip.py: synthetic 3-track board -- record
holds every item, the outside track flips back on restore, final
lock states identical to the original. Fails on v1.10.1, passes
here. The reroute_window stage is off by default, so no default
v1.10.1 run was affected.

## v1.10.1 -- 2026-11-24

Hygiene batch from the fourteenth external review (a follow-up audit of
v1.8.0..v1.9.6; findings filed as issues #43-#60, each verified against
the code before filing). Ten are fixed here; the engine-fairness items
(#45, #46, #51, #52, #57, #58, #60) stay open as their own batch.

- #43 Backend jobs now build their config from the plugin DEFAULTS --
  one source of defaults. Since v1.9.5, backend runs (self-tests
  included) silently ran WITHOUT the default engine portfolio, and
  with reroute_accept's dead 'faults' fallback, because the server
  template, the dialog overrides and run_job's forwarding never
  carried the keys. cfg_effective records the full effective config,
  and the self-test status flags jobs recorded without the defaults.
  Verified live: self-test jobs on the new code record
  engine_portfolio=tm_relax, reroute_accept=connectivity.
- #44 TraceMaker TOFU hardened. Version-less archives are refused;
  the binary's reported version must equal the layout version the
  pre-install hash check used (before, a crafted archive replaced the
  trusted install BEFORE the mismatch raised -- reproduced against
  the old code with the reviewer's recipe; the patched code refuses
  before install with the trusted tree intact); the existing install
  is moved aside for rollback, never deleted first.
- #47 The stitch-bridge 8 mm pre-filter measured distance to a goal
  polygon's OUTER RING only, rejecting a pad sitting over its own
  net's inner plane (true distance 0) whenever the plane outline was
  >8 mm away -- the exact case the v1.8.1 1-via drop was built for.
  The filter is now containment- and hole-aware. Fixture test: the
  old worker returns False on the synthetic board, the new one
  bridges (tests/test_bridge_filter.py).
- #48 tool_route tags the stuck corridor with the route OUTPUT board;
  v1.9.6's #38 fix tagged the pre-route board, discarding valid
  corridors and risking a mismatched one after a restore.
- #49 Layer-law names are resolved ONCE per board: the worker summary
  carries a layer-alias map (board names + canonical, case-insensitive)
  and TraceMaker's command line, the gate census and the layer-law
  policy all consume the resolved canonical names. A name matching
  no layer on the board is vacuous there -- the standing server
  config names the 4-layer GND plane, and a 2-layer board must not
  fail for it -- and every consumer logs it loudly, instead of the
  old behaviour where three of the four silently dropped it (a ban
  written 'GND' or 'in1.cu' previously banned the layer only in the
  finisher).
- #50 A windowed re-route no longer leaves its lock flags in the
  delivered board: cmd_lock_window records original states by UUID
  and never unlocks user-locked copper; the output's locks are
  restored before gating.
- #53 The backend image pins the KiCadRoutingTools native router to
  release v0.24.2 (it was fetched from the LATEST release against a
  pinned Python commit, unhashed) and shapely to 2.2.0.
- #55 Engine subprocesses run in their own process groups (a timeout
  kills the tree, not just the worker's direct child), and the
  pipeline's worker timeout for engine commands (3900 s) now exceeds
  the engines' own timeouts (2400/3600 s) instead of firing first.
- #56 engine_portfolio job overrides are whitelisted, de-duplicated
  and capped at 4; non-numeric routing values are dropped at config
  construction instead of crashing the job before the clamps run.
- #59 _accept_key treats a missing unconnected count as worst, not
  perfect (the mirror image of C1).
- #54 First committed test suite (tests/): layer-name resolution,
  the backend config build, the stuck-corridor tag (the real
  tool_route against stubs), and the stitch-bridge fixture.

## v1.10.0 (2026-11-24) -- Phase 2 begins: fault census + exact finisher checking (issue #41)

Phase 2 opened with a measurement, not a mechanism. The P2.0 fault census decomposed 60 production run ledgers (all 43 copper-only PCBWorld-Bench losses, the one connectivity loss, and 16 clean controls) at three gates -- base (the stripped input), routed (the restored best), and final (the delivered board). The finding: the pipeline added zero net copper faults on every sampled job. The 43 losses' 416 faults were already present on the stripped inputs and are placement geometry that routing cannot change (solder-mask bridges 221, copper edge clearance 137, annular width 56, drill out of range 2). Re-scored against each board's own base (connected and no dirtier than the base), the bench reads easy 1.00, medium 1.00, hard 0.90, against the raw clean-pass 0.68 / 0.50 / 0.30 -- so the v1.9.5 entry's attribution of clean-pass losses to the repair layer was wrong, and this entry corrects it. Router-owned copper is real but located elsewhere: re-route trades, engine-stage dangling tracks, and the graze band below.

- **Exact checking in the finisher (#41, P2.1).** _new_items_clean -- the last check before a finish/heal/stitch leg is drawn -- modelled copper-to-copper clearance exactly but was blind to three DRC families. (1) Keepouts: it sampled the segment centreline every 0.1 mm with no half-width, so a leg whose copper overlapped a rule area while its centreline stayed outside passed here and faulted at the gate; the test is now exact segment-to-polygon distance at half-width. (2) Edge.Cuts: new tracks and vias are now tested against the board outline at the design-settings copper edge clearance. (3) Drill holes: foreign pad and via drills are collected per call; new tracks keep hole clearance from every drill, and new vias keep hole and hole-to-hole distances. A synthetic fixture battery proves the old checker passed four graze cases (keepout overlap, edge graze, track and via past an NPTH hole) that the new one rejects, with no over-rejection on clear cases. Board-level A/B, patched vs v1.9.6 on four boards: final gates identical on three (the in-house board landed +2 copper in one run, inside the engine's run-to-run variance -- its own control landed 14 against a baseline of 18 the same night), reverted finisher attempts fell on both boards that had any (in-house board 4 to 2, mikoto 7 to 5; 11 to 7 total), and wall times were unchanged (worst ratio 1.29x). Also measured and closed without a code change: the engine's escape variants (--escape-plan / --escape-flow) do not beat the strict engine on dense boards (identical or worse gates; the flow variant produced no candidate), so no new portfolio engine ships.
## v1.9.6 (2026-11-23) -- hotfix: portfolio skip guard, re-route corridor, sliver accounting (issues #37-#40)

A review of the Phase 2 plan against this code found four defects in v1.9.5. All four are fixed here, before Phase 2 begins, so the Phase 2 fault census does not measure on top of them.

- **Portfolio skip guard (#37).** v1.9.5 skipped the TraceMaker passes whenever the portfolio winner's split_nets was empty -- with no fault check, and split_nets excludes GND by construction, so a winner carrying copper faults (a --relax candidate regularly carries 1-16) or even a split GND pour locked its faults in unseen. The skip now requires a fully connected winner (zero unconnected, no split nets of any kind, pour splits included) AND fault_count(winner) <= fault_count(base board); the base board is gated once per AUTO run for the comparison. Related staleness fixed in passing: after the portfolio stage the winner is re-gated whenever last_gate holds a losing candidate's gate.
- **Key layering documented, not changed (#37).** The same review flagged tool_portfolio selecting faults-first while pass selection is connectivity-first. That layering is deliberate: faults-first candidate selection is what kept KRT's 0-unconnected / 430-1142-copper candidates from winning the Phase 1 A/B. The code now carries a comment saying so; unifying the keys without re-running that A/B would trade a measured behaviour for a tidier diagram.
- **Re-route corridor + protection (#38).** tool_reroute_window read the stuck corridor from the last TraceMaker pass even when the board had since been restored to an earlier best, and wrote its advice with first_nets empty. The corridor is now used only when it was measured on the current board (the pipeline records which board each stuck report belongs to), and the target net is protected via first_nets: [net].
- **Sliver accounting + finalize delta (#39).** copper_sliver counted toward clean-pass copper but was absent from FAULT_CLASSES, so the acceptance key could not see repair steps creating slivers. It is in FAULT_CLASSES now (COPPER_CLASSES is the same set). finalize() records the final refill's fault delta in the ledger (fault_delta) and logs any change.
- **Hygiene bundle (#40).** Gate calls pass --fast to the worker summary, skipping the MST-crossing / RUDY congestion computation the gate discards. cmd_freeroute and cmd_krt_route clean up their temp dirs; krt_route also copies the .kicad_dru sidecar and refills its output, matching freeroute. _route_variant logs a nonzero TraceMaker exit code. POST /api/config accepts the Phase 1 routing keys (they were silently dropped). Dialog fallbacks match the config defaults (auto / In1.Cu). The summary layer census skips tracks on off-stack layers instead of raising KeyError.

## v1.9.5 (2026-11-23) -- Phase 1 completed: engine portfolio, PCBWorld-Bench, and the Phase 1 A/B verdicts (issues #33-#36)

v1.9.0 shipped the Phase 1 mechanisms behind config flags, pending measurement. This release is the measurement: the three outstanding A/B programmes and the bench runs are complete, two defaults change on the evidence, and the multi-engine portfolio ships. With this release the eleventh review's Phase 1 is complete.

- **Engine portfolio (#33, Phase 1.2).** A new AUTO stage runs before the TraceMaker passes: each configured engine routes the stripped base, every candidate is gated, and the best acceptance key seeds the pipeline; a fully connected winner skips the TraceMaker passes entirely. Engines: freerouting (Specctra DSN/SES round trip via the Freerouting 2.5.0 JVM distribution -- the dedicated native CLI build is broken upstream with a GraalVM serialization error), krt (KiCadRoutingTools autoroute at a pinned commit), and TraceMaker flag variants tm_relax / tm_global / tm_escape. The backend image bundles Freerouting (sha256-pinned) and KRT; both are optional at runtime -- an absent engine is skipped, never fatal. Measured in a 7-board A/B (portfolio freerouting,krt,tm_relax vs control): the portfolio arm never lost a final gate and won five -- bitaxe 14 to 3 unconnected, the 4-layer self-test board 1 to 0 and faster (the winner skipped the passes), walclu 3 to 2, harmalkar 2 to 1, mikoto 39 to 37. Attribution from the run ledgers: tm_relax won the candidate selection on all 7 boards; KRT connected everything on 6 of 7 boards but at 430-1142 copper faults and was never selected; Freerouting was never selected and produced banned-layer tracks on 4-layer boards (its DSN export marks the inner layers as planes). The default is therefore engine_portfolio='tm_relax'; freerouting and krt ship as opt-ins.
- **PCBWorld-Bench (#34, Phase 1.1).** A harness mapping the PCBWorld D3 tiers onto the local converted pool (coverage: easy 99/99 train and test, medium 285/286 train and 10/10 test, hard 291/292 train and 10/10 test) and running boards through the production pipeline, reporting clean pass, routability, violations, wirelength, vias and time. Full easy/test (99 boards): clean pass 0.68, 99/99 fully connected, and every failure copper-faults-only (median copper 0, worst 16). Test-split samples: medium clean pass 0.50 (10/10 connected), hard 0.30 (9/10 connected). Connectivity is effectively solved on this corpus; clean-pass losses are the repair layer's copper faults. Our clean-pass definition (unconnected 0 AND copper 0) is stricter than the paper's protocol, so these numbers are not directly comparable to the published table.
- **Windowed re-route verdict (#35, Phase 1.5).** The full-AUTO integration A/B (4 boards x control / faults / connectivity arms): the 'faults' acceptance key accepted zero re-routes -- gates identical to control at +500-700 s per board, an expensive no-op -- while 'connectivity' produced the stage's only win (bitaxe 14 to 10 unconnected, 13 to 8 split nets, at +14 copper). The reroute_accept default flips to 'connectivity'. The stage itself stays off by default (reroute_window_cap=0): one winning board in four does not justify an extra engine run on every board.
- **Priority advice, repetition 2 (#36, Phase 1.3 close-out).** The second repetition of the 7-board advice A/B reproduced repetition 1 exactly: identical promote/priority gate pairs on 6 of 7 boards in both repetitions, and mikoto marginally favouring 'promote' in both (39/30 vs 40/33). 'promote' stays the default; 'priority' remains an opt-in with no measured benefit.

Measured before shipping: an AUTO smoke on the hbridge self-test board with the portfolio enabled -- the tm_relax candidate keyed (0, 0, 0), the TraceMaker passes were skipped, and the delivered board gated 0 unconnected / 0 copper / 0 banned-layer tracks. Freerouting and KRT round trips on the same board gated identically to the pipeline's own result (0 unconnected) in 7.5 s and 15 s respectively.

## v1.9.0 (2026-11-23) -- Phase 1 of the eleventh review (engine diagnostics, advice reform, windowed re-route) plus the review's remaining correctness/hygiene batch (issues #21-#32)

Phase 0 (v1.8.2) fixed the correctness of the machinery. This release implements the review's Phase 1 items that measurement supported, and closes the open Medium/Low/security findings that were verified in the same audit. Two Phase 1 mechanisms ship behind config flags with their defaults unchanged, because the measurements say so; the bench harness and the multi-engine portfolio are Phase 1 items still in progress and are not in this release.

- **Engine diagnostics (#21).** tool_route requested the router's JSON output for a year and then regex-parsed its log text. It now parses route<N>.json and stuck<N>.json into a structured record -- unrouted pairs as REF.PIN endpoints with the engine's failure reasons, per-connection corridor rectangles, route statistics -- and passes --cut-report. The log-regex path remains as a fallback when the JSON is missing or malformed. Parser verified against a real stuck report from a production run.
- **Priority advice (#22).** A second pass-advice scheme, pass_advice='priority': instead of promoting failing nets into the protected first_nets set, the pass loop builds priority pairs from the stuck report (most-failed first, then shortest) and can attach reserve_for region advice from the engine's own corridors (advice_regions, off by default). Measured in a 7-board, 2-arm A/B against the existing 'promote' scheme: 6 of 7 board pairs produced identical final gates and the seventh differed by one unconnected item, inside the engine's own run-to-run variance. No measurable difference, so 'promote' stays the default and 'priority' ships as an opt-in; the structured diagnostics it is built on are the durable gain.
- **Windowed engine re-route (#23).** A new AUTO stage, off by default (reroute_window_cap=0): for a split signal net, every track and via whose bounding box misses a window around the stuck connection (the engine's corridor when known, else the net's own extent, plus reroute_margin) is locked, and the engine re-routes the board with --reroute, honouring the locks. Acceptance is by the configured key: reroute_accept='faults' (the step key) or 'connectivity' (unconnected first). Fixture-measured: a window re-route closed nets the finisher has never closed (-2 to -3 unconnected items) at +12 copper faults -- the faults-first key reverts that trade, the connectivity key takes it, and the split set changes rather than merely shrinking (bystander nets in the window can come back split). It ships opt-in pending a full-AUTO integration A/B.
- **JSON gate (#28).** The gate parses kicad-cli's JSON DRC report (--format json) instead of regexing the text report, whose layout is a display detail. Verified class-by-class identical to the text parse on fixture boards -- totals, unconnected counts, full violation-class dicts and the pour/signal split all match -- and the text path remains as a fallback.
- **Self-test verdict (#24, review M11).** The backend self-test verdict ignored copper violations entirely: a board could regress to hundreds of copper faults and still PASS. Recorded baselines and the verdict now include copper_violations, with a coarse band (baseline + max(10, 25%), or selftest_tolerance.copper) because copper counts are variance-prone under variant racing. Baselines recorded before this release carry no copper field; their verdicts pass flagged copper_checked=false until re-recorded. Unit-verified: a 10x copper regression reads DRIFT, an in-band result PASSes, a legacy baseline PASSes flagged.
- **Backend job table lock (#25, review M12).** The jobs dict was mutated by HTTP threads, worker threads and the retention pruner with no lock; under the 16-concurrent-job grid load that is a RuntimeError waiting for the wrong iteration. All mutations and iterations now go through a lock or a snapshot. Verified with a multi-thread submit/prune/read hammer: zero errors.
- **Retention fallback (#26, review L11).** _prune_retention's missing-key fallback was 20 runs -- the value that silently deleted grid runs earlier in this project's history. It now matches the config-template default of 200.
- **Default mode (#27, review M16).** The plugin config defaulted to mode='llm' while every published measurement is AUTO. The default is now 'auto'; 'llm' remains an explicit choice.
- **Layer-law resolution (#29, review M4).** Three inconsistencies: tool_route's fallback law banned In1+In2 while every finisher banned only In1 (with a silent config, the engine and the finishers were enforcing different laws); the layer-law policy compared the law as a raw string, so spacing variants silently disabled it; and the finisher silently dropped --no-tracks-on names it could not resolve canonically, leaving that layer simply unbanned. The fallback now matches, the policy compares normalized name sets, and the finisher resolves non-canonical names against the board's actual layer stack (board names and positional canonical names, case-insensitive) and raises on names that resolve to nothing -- verified both directions: a bogus name fails loud, a lowercase canonical name resolves and the leg routes.
- **Backend auth (#30, review S5).** Token checks are now constant-time (hmac compare via secrets.compare_digest) with an exact cookie-pair match, and fail closed when no token is configured -- the old substring test degenerated to accepting any air_token= cookie when the configured token was empty. Verified over HTTP: valid Bearer and cookie pass; wrong token, a suffix-extended cookie value and a prefixed cookie name -- the last two accepted by the old test -- all return 401.
- **Job parameter clamping (#31, review S6).** Job overrides are untrusted input to a shared backend: run_job now clamps the numeric routing parameters (max_passes 1-8, work 1e5-5e8, finish caps 0-32, ilp_repair_cap 0-8, reroute_window_cap 0-16, reroute_margin 0.5-50) and drops unparseable values. Verified in a live job's recorded effective config.
- **ILP fallback (#32, review L2).** The orchestrator's ilp_repair_cap fallback was 6 where config and pipeline use 0, so ILP repair could run when the config never enabled it. Now 0 everywhere.

Measured before shipping: the advice A/B above (rep 1 of 2; rep 2 runs as background confirmation); the windowed re-route fixture runs under both acceptance keys; the JSON-vs-text gate comparison on two fixture boards; the cleanup batch's unit tests as itemised; and a full AUTO run of the hbridge self-test board on the new code (0 unconnected, 0 copper violations, 0 banned-layer tracks).

## v1.8.2 (2026-11-23) -- Phase 0 correctness batch from the eleventh external review (issues #13-#20)

An eleventh external review of v1.8.0 reported one Critical, five Highs and a tail of Medium/Low and security findings. Every claim was verified against the code, live run artifacts or the cited sources before implementation (one High -- the claim that post-pass gates run under KiCad default rules -- was refuted by artifacts: the router writes the project file with the board's real netclasses and every save chains it; its two true residues are fixed here under #17). This release is the review's Phase 0: correctness of the machinery, no new routing capability.

- **Scoring key (#13, the Critical).** _final_key and the AUTO pass-loop key used `unconnected_total or 9999`, which maps a fully connected board (0) to the worst possible key: a later perfect pass could be restored over by an earlier worse one, and a layer-law escalation that connected everything lost to the split variant. Both sites now use a None-safe key (missing stays worst, 0 is best). Unit-tested: gates of 0, 3 and missing sort in that order.
- **Step outputs (#14).** heal wrote every pour net to the same healed.kicad_pcb, so from the second net on a gate-revert "restored" the very file the step had just overwritten -- rejected bridges stayed on the board while the ledger recorded a revert. The same pattern existed in stitch's second call, the ILP repair rounds and prune. Every step now writes a unique output file, and the ILP leg loop does too. Verified on a 5-pour-net fixture: five distinct outputs, each with its project sidecar.
- **Gate integrity (#15).** All post-pass gates reused one DRC report filename, the kicad-cli exit code was never checked, and a failed DRC silently re-parsed the previous board's report as the new board's gate. Reports are now unique per gate, the exit code raises on failure (verified: DRC returns 0 on violating boards, so this fires only on real failures), and each gate records the sha256 of the board it measured.
- **Acceptance (#16).** Step acceptance compared copper faults only; a step that left the board less connected was kept, and prune was not gated at all. Acceptance is now lexicographic -- (copper faults, unconnected items, split nets) must not get worse -- and prune is gated the same way. First live exercise: a heal bridge that cut unconnected items 4 -> 2 but added 2 copper faults was reverted on the fault element, exactly as the old rule would have done.
- **Rules propagation (#17).** The pipeline now copies the input .kicad_pro and .kicad_dru next to every board it produces or gates (before, the .pro travelled only as a side effect of other tools and .dru never travelled at all). The backend accepts an optional .dru upload and returns result.kicad_pro / result.kicad_dru beside the result board; the plugin downloads them next to the result.
- **Dialog, metric, reported gate (#18).** Cancel raised Cancelled inside its own handler (the handler called the same log function that raises once the event is set, and the worker thread's handler then died on a second raise); UI-thread logging no longer raises. violations_excl_unconnected double-subtracted: the KiCad header total already excludes unconnected items (proven arithmetically on a live report), so the field now equals the header total. The reported final gate is measured on the delivered final board, not on the pre-finalize working board.
- **Backend security (#19).** The TraceMaker update compared the TOFU hash only after the downloaded archive had been extracted, its binary executed for a version smoke, and the trusted install replaced -- with no rollback. The version is now read from the archive layout and the hash compared before any extraction, execution or install; a known version with a different hash is refused with the install untouched (verified with a marker-file test: the mismatched binary never ran). Tar extraction uses filter='data'. The config page escapes job board names in the jobs table (stored-XSS via a planted board name).
- **Config forwarding (#20).** run_job forwarded a hardcoded list of config keys -- the failure class that silently dropped the dataset ordering axis for two dataset generations. It now forwards any key in the plugin's config schema (server-owned keys excluded) and records the effective merged config (minus the API key) in the job status.

Measured after the fixes: a backend AUTO run of the Garage133 pour board reproduces the v1.8.1 result exactly (0 unconnected, 0 split nets, 0 banned-layer tracks, 12 copper violations, 56 vias, 686.0 mm) with the layer-law policy firing, the result sidecars present and the reported gate measured on the delivered file; the finisher spot checks match v1.8.1 (the /~{S5} closure retained, /RAM_A14 rolled back as before); the 5-net heal fixture closes 3 nets with 6 bridges under the new acceptance rule.

## v1.8.1 (2026-11-23) -- five fixes from the tenth external review, each verified and measured before shipping

A tenth external review of the v1.8.0 tree reported five bugs. Each was verified against the code (and, for #5, against the dataset-v3 job logs) before implementation, then measured on the bench fixtures and the dataset-v3 pour-only finals. Tickets: issues #8-#12. Two of the five were worse than the review stated, and the mechanism behind #2 also explained a standing timeout artifact from the v1.8.0 measurements.

**#5 -- backend jobs could not set two config axes at all (issue #8).** docker/run_job.py built its config with a hardcoded key list that omitted layer_law_policy and first_nets_policy, and defaulted no_tracks_on to 'In1.Cu,In2.Cu' where the plugin defaults to 'In1.Cu'. Both consequences are verified from the dataset-v3 grid job logs: the v1.8.0 layer-law policy never fired on any backend job, and the grids' ordering axis never ran -- the 'none' and 'most_pads' jobs both logged the 'auto' first-net set, so those arms were replicate runs of one effective config (the layer-law and max_passes arms were wired and their numbers stand). run_job now passes both axes through from the job config and defaults the law to the plugin's. Also: the result endpoint served the ledger of whichever pipeline workdir sorted last, so after a layer-law escalation that kept Variant B it served Variant A's ledger; run_job now copies the winning pipeline's ledger into the run directory and the server prefers it.

**#1 -- bridge candidates were half duplicates (issue #9).** _bridge_candidates deduped endpoint pairs with a DIRECTIONAL key, so (P,Q) and (Q,P) both survived and the 6-candidate cap held at most 3 distinct pairs, each attempted twice (the search is undirected, so the retry is guaranteed-identical). The key is now canonical (sorted coordinate pair), a diversity stride prefers pairs whose endpoints are not within 1 mm of an already-picked pair, and _comp_points coalesces duplicate points per fragment (polyline bend vertices were traced twice; a track endpoint coincident with a via appeared twice with different layer sets). Ten-net finish battery: every status identical to v1.8.0, including the /~{S5} closure.

**#3 -- keepbacks stamped after endpoint freeing; blocked goals flooded the search (issue #11).** Four planning sites (heal's bump ladder and via hunt, _bridge_plan, _finish_attempt's re-bridge loop) stamped edge/hole keepbacks AFTER freeing the start/goal discs, re-blocking endpoints near a board edge or hole; they now stamp in the finisher's order. Grid.astar and Grid.cutset_astar also built their goal sets without excluding blocked cells: a blocked goal can never be entered, so the search flooded every reachable cell before answering. Goal sets are now filtered (cutset returns early on an empty goal set).

**#2 -- heal's via hunt searched the wrong island, then thrashed (issue #10).** _island_polys attributed a plane polygon to a fragment by proximity alone, so a fragment point near the MAIN plane fill put the main polygon in the island set; the hunt then sampled via candidates over board-spanning polygon bounding boxes and its wins were main-to-main bridges, discarded as no_merge. Attribution is now by ownership: a polygon counts only if it contains one of the fragment's own anchor points (its vias and through-hole pads). The hunt is symmetric (if the start side's island yields nothing, the target side's island hunts back), candidate sampling is clamped to the hunt window (pair bounding box +-12 mm) with the via-block grid check BEFORE the polygon containment test, and the hunt's A* runs under an expansion budget. Measured: the corpus board whose heal timed out under both v1.8.0 arms now completes in 50 s (1 unconnected item left); a fragment pair that v1.8.0 false-discarded in 6 s now answers no_path in 13 s after a real search; the A2 3V3 fixture still closes fully (3 bridges, gate 0 unconnected). Known limit, unchanged: heal as a whole still has no per-attempt time budget -- one corpus board's full heal takes ~15 min of bounded attempts.

**#4 -- stitch bridges could not use one via, and host-less pads never tried (issue #12).** _bridge_island collected goals only on the pad's own outer layer, so a legal drop to the net's inner plane (one via) was unreachable; goals are now collected on every layer the net fills, with inner layers reached through the own-plane exception -- 1-via bridges are reachable (the 2-via rejection cap is unchanged). The bridge grid is built with the net-class via radius (it planned at a fixed default radius before) and stamps edge/hole keepbacks before freeing endpoints. cmd_stitch never called the bridge for a floating pad with no containing outer-layer zone; those pads now fall through to it, and a minimum-distance pre-filter skips building a grid when every goal lies beyond the bridge length cap. Measured on the pour-only finals re-run: outcomes match v1.8.0 board for board, including the slowest board (0 stitched / 7 failed in ~1340 s under both versions).

Regression battery on this tree: finish battery 10/10 statuses identical to v1.8.0 (S5 closure retained); heal -- the timeout board completes (above), reljamar partial gate 5 (unchanged), eric-wood full closure retained, mengxyz partial gate 3 -> 2; stitch outcomes unchanged; A2 fixture gate 0; MaxKiv long_finish 3/3 routed; backend wiring verified live (layer-law policy fires via run_job, first_nets override honoured, winning ledger served); backend self-tests all pass.


## v1.8.0 (2026-11-23) -- four improvements from the ninth external review, each verified and measured before shipping

A ninth external review proposed four improvements. Each was verified against the code and the dataset-v3 grid data before implementation (two of its four evidence hooks needed correction, recorded below), then measured on the bench fixtures and on the 22 dataset-v3 pour-only finals. Tickets: issues #4-#7.

**#1 -- bridge endpoints from all copper points (issue #4).** The victim re-bridge loops in `_finish_attempt` and its plan-only twin `_bridge_plan` chose endpoint pairs from PADS only, although `_comp_points` (pads + vias + track endpoints per fragment) already existed for heal. Both now draw candidates from `_bridge_candidates` (up to 6 closest cross-cluster copper-point pairs + the largest-cluster pair). The review's flagship premise does NOT survive measurement on its named board: on post-rip srambo /RAM_A12 the closest copper points are 18.0 mm apart (pads 22.8 mm) and a stub-window A* still finds no path -- that wall is genuine congestion. But on srambo /~{S5} the richer endpoints found the re-bridge pad pairs never could: rolled_back in v1.5.0, v1.6.0, v1.7.0 and v1.7.1 -- **routed** here, the first finisher closure of a named residual in this programme. Ten-net battery (srambo + Manikin): 1 closure, 9 status-identical; the new arm is slower on failures (more candidate pairs per round).

**#2 -- pour repair capacity (issue #5).** Three parts. (a) `_bridge_island` may now cross a trace ring with up to 2 vias; its drawing block also never actually drew vias and forced every segment onto the pad's layer -- both fixed. (b) `cmd_heal_attempt` plans on a clearance-bump ladder (0 / +0.05 / +0.10 mm), accepting only plans that pass the exact checker, and stamps edge/hole keepbacks like the finisher: the planner/checker graze band is why heal returned no_path on bridges that exist. (c) AUTO runs stitch+heal once more AFTER prune. Measured on the 22 pour-only finals (83 unconnected items): stitch with (a) -- one attributable partial closure; a fresh heal pass on the finals -- 83 -> 61 with the STOCK heal (three full closures: the mid-sequence heal leaves these because prune and the finish stages change the board after it runs), and 83 -> 58 with (b) added (one further full closure, two better partials; one board is a timeout artifact under both). The in-house A2 3V3-split fixture: stock heal bridges 2 of 3 fragments (1 unconnected left); the upgraded heal bridges all 3 -- fixture gate 0 unconnected. Known limit, unchanged: heal has no per-attempt time budget, and one corpus board's fragment pair thrashes the via hunt for minutes under both versions.

**#3 -- layer-law policy in AUTO (issue #6).** On boards with >= 4 copper layers and a pour net on In2.Cu, AUTO now runs Variant B (both inner layers track-banned) first and escalates to Variant A only when B leaves split SIGNAL nets, keeping the better final by the #4 key. Grid evidence (54 multilayer train boards): fixed A totals 334 unconnected, fixed B 340, per-board oracle 290; the shipped trigger simulates to 309, doubling runtime on the boards that escalate. Live spot-check (Garage133): the B run finished 0 unconnected / 0 banned-layer tracks, no escalation -- the A configuration leaves 2 on this board. Set `layer_law_policy` to anything other than 'auto' to disable.

**#4 -- best-pass key counts faults (issue #7).** Pass selection (and the #3 comparison) now keys on (unconnected, failing nets, fault_count) instead of (unconnected, failing nets). Correction to the review's evidence: the four boards it cited (unconnected 0, copper worse than the human board) had no clean pass in ANY configuration, so no key can rescue them; the fix is hygiene for exact ties, shipped as such.

Regression battery on this tree: A2 heal (above), stitch clean no-op on a healed board, MaxKiv long_finish 3/3 routed (unchanged), backend self-tests all pass.


## v1.7.1 (2026-11-22) -- three correctness fixes from the seventh external review (one premise corrected on cold review)

A seventh external review of the public v1.7.0 tree reported three worker.py bugs. Each was verified against the code and measured before fixing; the fixes were then cold-reviewed before the tickets closed. Tickets: issues #1-#3.

**Fix #1 -- Grid window filter (issue #1).** `Grid.__init__` collected a track only if its start OR end fell inside the window +-2 mm; a segment crossing the window with both endpoints outside was invisible to every planner. Now the bbox-intersection test (the keepout idiom). Measured incidence on real finish windows: 5 dropped-but-crossing segments across 9 windows; zero in the ceiling nets' windows -- real, small, and NOT the repair ceiling's cause. Bounded harm by construction: `_new_items_clean` scans the whole board, so a dropped segment could only waste an attempt, never pass bad copper.

**Fix #2a -- build_owners priced multi-halo cells (issue #2).** Cells stamped by >= 2 nets' clearance halos were marked HARD, so two minimum-spaced parallel tracks (0.2 mm tracks at 0.20 gap, ~0.33 mm halos) made the whole corridor between them an unpayable wall. `build_owners` now records per-cell owner BITMASKS and `cutset_astar` charges phi for every newly touched net in a cell. Measured on the flagship (post-rip srambo /RAM_A12 bridge window): hard cells 74,477 -> 58,904 (the multi-halo class, 21%) -- and the cutset STILL finds no path; the residual wall there is pads / GND / single-net copper, as the ticket stated up front. Where it does pay: Manikin /ADR_LED1 -- `_plan_attempts` returned ZERO attempts under the old code; the bitmask cutset now discovers the two-victim set {/ADR_LED2, /ADR_LED4} through the LED bus corridor, so finish_leg moves no_path -> rolled_back (the leg plans and executes; the victim re-bridge still fails). Closures: 0 under both versions -- the frontier for that net moved from planning to execution.

**Fix #2b -- CORRECTED PREMISE (recorded plainly).** The ticket also claimed victim pads were unstamped in build_owners, so cutset paths could cross them free. The cold reviewer disproved it from the v1.7.0 source: the pad loop stamped every pad with `net not in ignore_nets`, and the only call site passes `ignore_nets = {net}` -- the target only. Victims are the cutset's OUTPUT, never its input, so their pads were always stamped hard. The pad-loop change shipped here (`target` parameter, Grid.build semantics) is therefore a behavioural NO-OP at the only call site: defensive alignment for a hypothetical future caller, not a bug fix. The original verification error was checking the loop condition without checking the call site's argument.

**Fix #3 -- keepbacks in primary finish planning (issue #3).** `_stamp_edge_keepback` / `_stamp_hole_keepback` are now stamped in `_plan_attempts`' route_plan and in `_plan_path`, before free_disc, mirroring cmd_long_attempt's order and arguments. Previously a primary leg near an edge or NPTH hole could pass the worker check and die at the kicad-cli gate (slow revert). The `_new_items_clean` checker still does not test Edge.Cuts / NPTH clearance -- recorded in the ticket as a known remaining item; the planner stamps mean finish paths no longer propose such legs.

**Cold-review hardening (same release).** `build_owners`' `target` and `cutset_astar`'s `nets` are now REQUIRED arguments -- both defaults were silent-drift footguns for future callers (one call site exists; it passes both). Behaviour unchanged: the /RAM_A12 probe re-run on the hardened code is identical (58,904 hard, NO PATH). Two reviewer notes stand untested, recorded here: richer masks make cutset_astar's 8-label Pareto cap bind sooner in dense windows (soundness unaffected; completeness within budget may drop), and fix #1 enlarges the finish candidate set (unmeasured perf delta).

**Measurement (vs v1.7.0, same boards).** Heal fixture bridges 2; stitch clean no-op; a long_finish control board stays 3/3; the phase-(b) proof case intact (`_plan_attempts` srambo /~{CI} -> [('/AUX6', '/~{BE}')]); finish_leg battery identical on 9 of 10 nets with /ADR_LED1 the single explained delta above. Backend self-tests ALL PASS on the 1.7.1 image on both deployment hosts (in-house board: 2/2 and 1/1 unconnected/split respectively, banned-layer tracks 0 on both). Process note: the cold reviewer graded the battery claims UNVERIFIED because probe outputs were not persisted as files (it verified the self-tests live instead) -- persist outputs alongside claims from now on.

## v1.7.0 (2026-11-22) -- attempt selection vs PathFinder: the head-to-head (both measured zero; selection ships default-OFF as a diagnostic)

The owner asked for both remaining repair approaches to be built and compared, winner adopted. Result: **neither closes a single measured residual.**

**Track B -- bridge-aware attempt selection (this release).** `cmd_finish_probe` dry-runs an attempt (production partial rip + bump ladder, in memory) and requires a `_bridge_plan` (plan-only twin of the v1.6 bridge loop) for every victim fragment merge; `cmd_finish_leg` probes all attempts and orders bridgeable < unknown < not-bridgeable (stable). Two probe bugs were caught by its own controls before the measurement: a full-strip rip (corrupts this KiCad build's net-info AND answers the wrong question -- production rips only leg-violating copper) and a missing bump ladder (bump-0 legs graze post-rip). A third was caught in the harness: crashed probes (pcbnew degrades under multi-container load; a pair probe died at GetTracks() after _plan_path) were recorded as bridgeable by the first fallback -- now retry-once, then UNKNOWN, never promoted. Head-to-head vs v1.6.0 finish_leg, per-net on pristine boards: **srambo 6 nets + Manikin 4 nets, outcomes IDENTICAL on all 10, 0 closures**; selection engages on only 2/10 nets (the rest have a single attempt per round) and costs +14-39 s of probes where it engages. Ships behind config `attempt_select` (default False, the v1.5 ILP precedent): its retained value is the `attempts_probed` telemetry in finish_leg results, which decomposes a rolled_back into no_plan / leg_dirty / no_bridge per attempt without hand-run diagnostics.

**Track A -- PathFinder-style negotiated congestion (spike, NOT shipped).** History-escalated cutset negotiation (Phi x 2^history per round, <=15 rounds) on the bridge problem, scripts in the phasec-spike tree. srambo /RAM_A12: **FAIL-NO-PATH at round 1** -- even the soft search (single-owner cells priced, only pads/keepouts/multi-owner cells hard) finds no corridor between the fragments in a +-20 mm window; negotiation had no first path to escalate over. Manikin /SDA: unreachable -- its single attempt's leg is dirty at bump 0 and unplannable at bump 0.05 (LEG-DIRTY-ALL-BUMPS), a planner-resolution failure class neither approach addresses. Caveats: negotiation ran on the closest fragment pair only (the hard planner already failed all pairs), and multi-owner cells stayed hard -- pricing them means recursive multi-net restoration, out of spike scope.

**What the residuals actually are, after three zero stages (ILP, re-bridge, selection) and the spike:** (1) corridors walled by hard cells (pads/multi-net overlap at clearance-inflated stamps) -- no search over priced single-net displacement opens them; (2) legs that exist only inside the graze band between planner clearance and exact geometry (SDA) -- a planner-resolution problem; (3) single-attempt nets whose victim bridges fail inside production's own machinery. The repair-capacity line is therefore STOPPED at v1.7.0 on measurement, not on budget: the next lever for classes (1)/(3) is engine-side (the TraceMaker programme's lane), and class (2) is a finisher grid-resolution question for a future, separately-measured change.


## v1.6.0 (2026-11-22) -- victim re-bridge upgrade (measured: frontier moved, not crossed)

The re-bridge inside _finish_attempt was single-pair, hard-only,
no-bump, edge/hole-blind. It now tries up to 6 closest
cross-cluster pairs plus the largest-cluster pair, at +-12 then
+-20 mm windows, with the clearance-bump ladder and the edge/hole
keepbacks from long_finish. Safety is unchanged: every bridge
still passes _new_items_clean and the final cluster verification.

Measured on the two hardest beds: srambo /RAM_A14 rolled_back,
/RAM_A16 no_path; Manikin /SDA and /IO_1 rolled_back -- zero
closures, no regressions (heal fixture bridges 2, stitch clean
no-op). The value is diagnostic as much as mechanical: with the
blindnesses removed, the residual failure is precisely
characterised. For /RAM_A14 (victim /RAM_A12, fragments 22.8 mm
apart), even the cut-set A* finds NO bridge path in a +-20 mm
window -- the fragments are walled by multi-net congestion, so
no single-net-displacement bridge exists at any price. That is
the Stage C (negotiated congestion) brief, or bridge-aware
attempt selection (dry-run bridge planning per attempt before
executing). Neither is built; v1.6.0 ships the upgraded bridge
because pair-choice, graze and edge/hole blindness are proven
failure classes elsewhere (MaxKiv needed all three fixes in
long_finish), and the bridge no longer has them.

## v1.5.1 (2026-11-22) -- gate census own-plane exemption

banned_layer_tracks no longer counts a pour net's tracks on its
own plane layer(s). cmd_summary now also emits tracks_by_layer_net
and pour_layers (each pour net's zone layers); the gate exempts
own-plane pour tracks, which are legal by design (heal bridges).
Verified on the worst dataset-v3 case (walclu_smartReflow final
board): raw census 9 In1.Cu tracks, all GND heal bridges on GND's
own In1 plane -- corrected count 0. The 105 tracks flagged across
the v3 grid were all of this class; there were no genuine
layer-law breaches. banned_exempt_own_plane is recorded in the
gate dict for transparency.

## v1.5.0 (2026-11-22) -- Stage B ILP machinery + victim-pad planner fix

Two parts. The second is the one that matters today.

(1) Phase (c) Stage B, as specified: joint candidate selection for
co-windowed split signal nets. Each net contributes its finish
victim sets as priced candidates (A* cost + the phi victim
penalties), an exact geometric conflict graph is built (shared
victims, or paths closer than the DRC rule on a shared layer), and
a 0-1 ILP (scipy/HiGHS; greedy fallback) picks a conflict-free set,
executed cheapest-first through the existing finish_attempt child
process, with one re-plan round. Available as the ilp_repair tool
(worker CLI + LLM tool); the AUTO stage exists but
ilp_repair_cap defaults to 0 (OFF) -- see the measurement below.
The production image now carries python3-scipy for HiGHS.

(2) THE FIX: Grid.build no longer hides victim pads from the
planner. Ripping removes a victim's tracks and vias, never its
pads, and the exact checker tests drawn legs against all foreign
pads -- but build() stamped pads only for nets outside the ignore
set, so displacement planning threaded legs through the victim's
own pads and every such leg died at the exact check (root-caused
on srambo /TMS: the leg crossed U1.48, a pad of its victim /TCK,
SDF distance -0.011 mm). build() now takes target= and stamps pads
for every net except the routed one; all seven call sites pass it.
build_owners already treated pads this way. Also: finish attempts
gained the clearance-bump ladder (0 / +0.05 / +0.10) from
long_finish.

Measured (srambo_1 final board, the six residual nets): ILP round 1
(pre-fix) selected 4 legs, all failed at execution. Post-fix the
picture is honest: /RAM_A16, /TDO, /TMS have NO candidates at all
-- their pre-fix candidates existed only by threading victim pads;
/~{CI} has candidates but was not selected; /RAM_A14 and /~{S5}
were selected and still fail, at victim RE-BRIDGE (hard-only,
single closest pair, +-12 mm window -- the same wall finish_leg
hits; /TMS finish_leg now returns a fast truthful no_path instead
of rolled_back). Verdict: selection was never the binding
constraint; execution is. The ILP ships as machinery, off by
default, and the next repair-capacity work is the re-bridge:
multi-pair, larger windows, displacement-capable bridges.
Regressions: heal fixture bridges 2, stitch clean no-op, MaxKiv
long_finish still 3/3 (one at bump 0.05), finish /RAM_A14
unchanged (rolled_back, re-bridge class).

## v1.4.1 (2026-11-22) -- edge-keepback bbox fix + Stage B-0 fixture benchmark

Bug found by the Stage B-0 micro-benchmark, in code shipped hours
earlier in v1.4.0: `_stamp_edge_keepback`'s fallback for non-segment
Edge.Cuts shapes stamped the bounding box's two DIAGONALS plus its
left/right edges instead of the four edges. On any board whose outline
is a rectangle shape (common in exported designs) the planner saw an
X wall across the board interior and long_finish reported no_path for
nets it should have routed. Segment outlines (the in-house board, the MaxKiv
fixtures) were unaffected, which is why the ship tests missed it. The
fallback now stamps the four bbox edges.

Stage B-0 micro-benchmark (worker-level long_leg chains + kicad-cli
gates on 9 dataset-v3 repair fixtures, 41 split signal nets):
10 nets routed, unconnected 57 -> 48, one board fully closed
(MaxKiv_kibo__0, 3/3 nets, zero violation delta). Per board:
MaxKiv_kibo__2 2/3 (3->2, +1 starved_thermal the pipeline gate would
revert), SpaceTeam_EDU_RASPI_HATS 1/1 (5->4), jamesturton_bose 1/1
(4->3, only routable after the fix above), Manikin_Color_Hub 3/16
(22->17). No path on irudoy_cansult (proven congestion: no hard-grid
plan exists at any clearance bump), Leyden_Jar, harmalkar_satellite
and capitaneanu_bitaxe (0/14, dense fine-pitch). Read: long_finish
takes the far-apart class it was built for; the remaining residuals
are congestion/displacement class -- the ILP stage's brief.

## v1.4.0 -- phase (c) Stage B-0: long_finish (large-window hard A*)

New worker commands `long_leg`/`long_attempt`, pipeline `tool_long_finish`, an AUTO stage after the signal finish_leg loop (config `long_finish_cap`, default 6), and an LLM tool of the same name. For a split signal net it tries up to six closest cross-cluster anchor pairs plus the pair joining the two largest clusters, over a pair-bbox window (+-10 mm), on the HARD grid (no victims -- foreign copper is never displaced), drawing through the existing `_finish_attempt` (exact check, refill, cluster verification) and gate-verified at the pipeline exactly like finish_leg.

Motivation (dataset-v3 spike, 2026-11-22): a whole class of stranded nets has components tens of mm apart with a legal board-wide hard path that finish_leg's single-closest-pair planning never reaches. Nets with no hard path are congestion-class and stay for the ILP stage.

Three planner fixes the fixture forced, all inside the long path: a clearance-bump ladder (0 / +0.05 / +0.10 mm planner clearance) for grid-vs-exact grazes (a via landed 0.150 mm from a foreign track vs the 0.200 rule at 0.1 mm grid quantisation); `_stamp_edge_keepback` (band around Edge.Cuts segments at edge_clearance + w/2 -- without it the legs added +10 copper_edge_clearance); `_stamp_hole_keepback` (foreign drilled pads' holes at drill_r + hole_clearance + w/2 -- NPTH holes carry no copper for the pad stamper to see; tracks passed 0.244 mm from them vs the 0.250 rule). Both keepbacks are candidates to move into Grid.build for all consumers later, as their own measured change.

Measured: the MaxKiv_kibo__0 repair fixture (3 stranded signal nets, anchor pairs 106-124 mm apart) closes all 3 (one needed clr_bump 0.05), final gate 0 unconnected with ZERO violation delta against the input board. srambo_1 residuals /RAM_A14 and /TDO return no_path, as scoped. Heal fixture still bridges 2, stitch still a clean no-op.

## v1.3.3 (2026-11-22) -- Step 0 reconciliation deltas

The fifth-review mission spec (written against v1.3.0) asked for
three cleanups on top of the two criticals. Verified against the
v1.3.2 tree: the stitch fallback clearance was still a literal
(0.205) -- now `board_clearance(b) + 0.005`, so cmd_stitch is
fully board-derived. The other two were checked and DELIBERATELY
NOT done, on evidence: `_stamp_inner_via_blocks` is not provably
redundant (its ignore-set semantics differ per call site; several
via_block arrays are hand-built, not Grid.build-derived), and the
`fall` re-check in `_new_items_clean` is not a duplicate -- it is
the ONLY check covering through-hole pads, which live on every
layer. Both stay; the stamp's stale 2-layer-era comment now says
why. Fixtures unchanged (heal bridges 2, stitch no-op); large backend host
self-tests PASS, in-house board 1/1 with banned_layer_tracks 0.

## v1.3.2 (2026-11-22) -- layer-law enforcement + per-island containment (fifth review, verified)

A fifth external review of v1.3.0 found two critical regressions
from the phase (a) N-layer rewrite. Both verified line-by-line and
by live probes; both had already fired in shipped runs.

- **Critical #1 -- the finisher could route on the banned GND
  plane.** TH pads/vias seeded `Grid.astar` on EVERY stack layer
  and the search stepped tracks on the node's own layer unchecked;
  In1.Cu's blocked plane is nearly empty (TraceMaker never routes
  there, zone copper is not stamped), so heal bridged 3V3 fragments
  with tracks straight across the GND plane. KiCad's refill slots
  the pour around them, so DRC and every gate comparison read
  clean -- a layer census of the v1.3.1 large backend host self-test final found
  14 tracks on In1.Cu, all net 3V3. Fix: `astar`/`cutset_astar`
  take a per-call allowed set (routable layers plus, for a pour
  bridge, that net's own plane layer); starts/goals/via targets are
  filtered to it. The run's `no_tracks_on` is now plumbed to the
  worker (`--no-tracks-on`, child processes included), so variant
  layer laws bind the finisher, not just TraceMaker.
- **Critical #2 -- Y1's hole-aware containment tested the whole
  zone.** `SHAPE_POLY_SET.Contains(pt)` with one argument tests
  ANY sub-polygon (probed live: a point in outline 1 returns True
  for the set, False per-oi for oi != 1). `_island_polys` therefore
  collected every island including the main plane, `_bridge_island`
  could never populate its goals (silently disabled), and stitch
  hosts matched any island of the zone. All sites now use the
  per-subpolygon `Contains(pt, oi)` and carry `oi` through.
- **Gate layer census.** `cmd_summary` reports `tracks_by_layer`;
  the gate derives `banned_layer_tracks`, which counts into
  `fault_count` -- every gate-verified step (finish, heal, and now
  stitch, which previously accepted its output unconditionally)
  reverts banned-layer copper. The backend self-test verdict also
  fails on any banned-layer track. Stitch additionally checks
  no-via rule areas as discs and takes its clearance from the
  board (was a hard-coded 0.23; inner layers from `copper_stack`,
  was a hard-coded In1..In4 tuple).

Measured: heal fixture bridges 2 (unchanged) with zero In1 tracks;
finish /RAM_A14 rolls back cleanly; stitch no-op on the fixture;
the census reads exactly 14 on the known-bad v1.3.1 board. large backend host
self-test on 1.3.2: in-house board 1 unconnected / 62 violations with
`banned_layer_tracks: 0` -- the same headline v1.3.1 reached with
14 illegal tracks, now reached legally. J1 experiment re-run
legally (keepout-cleared board, its 14 In1 tracks stripped): the
board stands at 3 unconnected and the stock healer finds no legal
path -- the earlier 0-unconnected result was bought entirely with
plane breaches, and closing J1.1 legally needs displacement help
or a fresh route on the cleared board, not the additive healer.

## v1.3.1 (2026-11-21) -- cut-set victim discovery (v3 phase (b)) + P1/P2

Phase (b) of the adopted v3 roadmap (Stage A), plus the fourth
review's deferred performance items P1/P2:

- **Weighted cut-set A*** (`Grid.build_owners` + `Grid.cutset_astar`):
  the window grid is built once with foreign track/via copper SOFT --
  each cell records the single foreign net blocking it (cells shared
  by >= 2 nets, foreign pads, keepouts and GND stay hard). One A*
  whose labels are (cell, victim bitmask), Pareto-pruned, charges a
  one-time rip penalty per victim net (the standing victim policy:
  base + 10/pad + 1000 wide + 1000 power-named). The cheapest path's
  victim set is the minimum-penalty cut-set of ANY size -- closing
  the empty cut-set fallacy: two parallel tracks jointly blocking a
  corridor previously meant zero attempts were ever proposed.
  Scoped by measurement: the search costs ~1-3 min on large boards,
  so it runs only when the singles probe comes up EMPTY (legs with
  singles never needed it; their attempt lists are unchanged).
- **P1**: `Grid.__init__` window-filters pads/segs/vias/keepouts
  (+2 mm margin).
- **P2**: stitch slices its obstacle lists to each cluster's window
  once per attempt.

Measured (srambo residuals): /~{CI} has NO single victim -- v1.3.0
proposed no attempt at all; the cut-set found {/~{BE}, /AUX6}. The
end-to-end finish still rolls back: the victims' one-shot re-bridge
cannot jointly re-route them, which is precisely phase (c)'s brief
(windowed ILP re-routes target + victims together). /RAM_A14,
/RAM_A16, /TMS resolve to singles as before; /TDO has no priced
path within the search cap. Fixtures: heal bridges 2 (unchanged),
stitch clean no-op, finish /RAM_A14 rolls back cleanly with
planning at 34 s (was ~47 s; P1 + scoping).

## v1.3.0 (2026-11-21)

v3 phase (a) plus the correctness cluster (Y1-Y4) from the third
implementation review. Phase (a): the finisher grid is N-layer --
layer indices are stack positions from a new copper_stack() helper
(KiCad numbers copper out of stack order: F=0, B=2, In1=4, In2=6),
tracks on every copper layer are stamped, A* vias transition to any
routable layer (In1.Cu stays banned by the standing layer law),
victim deletion/re-bridging and the exact checker work per stack
layer, and heal can launch from inner-layer track endpoints. Also:
Grid keepouts now include FOOTPRINT-embedded rule areas (the J1
keepout lives inside the J1 footprint; the planner, hunt and checker
were all blind to it -- a hunt via landed 0.015 mm from its edge).
Y1: hole-aware containment (SHAPE_POLY_SET.Contains) in
_island_polys, _via_hunt (candidates + no-via areas) and
_bridge_island goals. Y2: _via_hunt restores the shared blocked /
via_block snapshots between candidates (free_disc mutation leaked
across candidates). Y3: _bridge_island plans at the drawn width
(the net's class width), exact-checks the bridge before committing,
and uses the correct stack index for B.Cu pads (phase (a) had left
it planning on In1.Cu's plane). Y4: real via radii in _finish_attempt
rip-up tests and cmd_stitch (which now places the net's class via);
_via_hunt clearance uses grid.clr_plan instead of a literal 0.23.
Measured: DSKY 2, srambo_1 6, tri_panel 84 unconnected, copper
identical to v1.2.5 on all four boards; the in-house board AUTO gate (1 /
62 / copper 15: hole/edge/thermal classes present from the first
TraceMaker pass) is unchanged -- an apparent in-house board copper
regression during development was a phantom baseline (v1.2.5
control run reproduced the identical 15). Fixtures: heal bridges
2, finish /RAM_A14 rolled back, stitch clean no-op.


## v1.2.5 (2026-11-21) -- the third review's four fixes

A third external audit (of v1.2.3/v1.2.4) found the exact checkers had
not kept up with the planner; all four findings verified against the
code and fixed:

- Shared pad SDF: _new_items_clean, _via_hunt and stitch now test
  foreign pads by the same true-shape SDF the Grid stamps (they used
  0.5*hypot discs), foreign vias at their real radius, and new vias
  at the net's own netclass via radius. The disc checker was silently
  vetoing bridges the v1.2.3 planner had made legal (fail-safe, but
  lost finishes). Heal fixture unchanged: bridges 2, gate 63/1.
- Stitch containment is hole-aware (SHAPE_POLY_SET.Contains instead
  of Outline-ring extraction, which ignored interior holes), the
  candidate scan is clamped to +-6 mm around the cluster's pads, and
  the sort buckets dmin to 0.1 mm so the distance tiebreak engages
  (float dmin never tied; far candidates burned refill+rebuild each).
- Backend run_job passes finish_signal_cap / finish_pour_cap through
  to job configs and writes the full gate field set to status.json.
- split_pour_nets gate field (split pour nets including 'GND');
  tool_heal and AUTO's pour finish/stitch/heal consumers key off it --
  keyed off split_nets, which hard-excludes 'GND', a via-carrying GND
  fragment was silently never healed.

## v1.2.4 (2026-11-21) -- the v3 audit's four verified fixes

Shipped after auditing ai_router_v3_proposal.md against the code (its
seven worker bugs verified line-by-line; two of its premises were stale:
dataset-v2 was already harvested, and its Phase 1 gate was already
half-answered by the v1.2.3 comparison runs). Kaushik adopted the v3
roadmap as the replacement for the earlier P1/P2 plan.

- split_signal_nets: gates now also report split nets excluding the
  board's ACTUAL pour nets (the old split_nets excludes only the literal
  name 'GND'). split_nets itself is unchanged -- AUTO derives pour
  finishing/healing from split_nets x pour_nets.
- Diagonal corner-cut guard: grid A* diagonal steps now require both
  orthogonal neighbours free on that layer (a diagonal passes within
  p/sqrt(2) of a blocked corner).
- finish_leg checks its leg and every victim bridge with the exact
  geometry checker BEFORE materialising them: a faulting attempt fails
  in-worker and the next victim set is tried, instead of the pipeline
  reverting a DRC'd board and discarding the remaining attempts.
- stitch: candidates must also pierce the net's inner plane fill
  (polygons pre-extracted once per net) -- the old loop discovered
  non-piercing candidates by refill+rebuild each (up to ~90 refills per
  pad; the ~11-minute in-house board stitch).

Deferred to the v3 roadmap: 2-victim cut-set probing, multi-layer
finisher grid, stitch single-refill commit, ILP windowed repair.

v1.2.3 vs v1.2.2 comparison (large backend host, shipped AUTO config): CH330 1 -> 0
unconnected; DSKY 2 -> 2; srambo_1 6 -> 6; tri_panel 84 -> 84; in-house board
self-test 1/1 on both backends. No regressions; the hard boards are
repair-capacity-bound, which is what the v3 phases target.

## v1.2.3 (2026-11-21) -- P0: finisher geometry + gate metrics (review plan adopted)

Implements P0 of the adopted improvement review, plus the four fold-ins
from the v2 proposal review. The multi-layer finisher grid from the
same review is NOT here -- it is a grid rewrite and lands with P2.

- **True pad shapes in the finisher grid.** Pads are stamped by their
  real shape (rect / roundrect SDF, oval capsule, circle) instead of a
  disc of radius 0.5*hypot(w, h). On the in-house board, pads-only blocked cells
  drop 22% (51,120 -> 39,634 in the test window); the planner now sees
  the channels and via sites the disc was closing at fine pitch.
- **Rules from the board.** Planning clearance is the board's largest
  effective netclass clearance + 0.03 (was hard-coded 0.23); vias the
  finisher/healer place use the net's netclass via diameter/drill (was
  fixed 0.6/0.3); existing vias stamp at their real radius; the exact
  checkers use the derived board clearance (was a literal 0.20).
- **A\* heuristic.** The grid search was plain Dijkstra; it now uses an
  admissible octile heuristic. (First cut shipped an f-vs-g staleness
  bug that silently returned no-path everywhere -- caught by the heal
  fixture before release; the heap now carries (f, g, node).)
- **Gate metrics.** `violations_excl_unconnected` is honest now (was a
  copy of the total). Gates also record `copper_violations` (copper
  classes only, excluding unconnected), `vias` and `wirelength_mm`;
  AUTO records a base gate on the input board so later gates read as
  deltas. The finish/heal fault guard now counts 14 DRC classes (was
  3), including tracks_crossing, hole_clearance and track_dangling.
- **AUTO finish caps are config** (`finish_signal_cap` 8,
  `finish_pour_cap` 4; were hard-coded 4 / 2).
- **PLAYBOOK correction:** the LLM prompt no longer states a fixed
  stackup / unroutable In2; it reads the stackup from board_summary.

Fixture regression: heal on the in-house board A2 fixture = 2 bridges, gate
63 / 1 unconnected, identical to v1.2.2. Self-tests PASS on both
backends; in-house board self-test last = 1 unconnected / 1 split on both.

## v1.2.2 (2026-11-21) — patch our own router, not a fork

Direction set by the owner after reviewing an external "PCBWorld-H"
proposal: no LG fork — its transferable ideas land here instead.

- **Netclass-aware widths (worker):** finish legs, victim re-bridges
  and heal bridges are now planned AND drawn at the target net's own
  effective netclass width (`NET_SETTINGS.GetEffectiveNetClass`),
  threaded through the planning grid's clearance inflation and the
  exact-geometry checker. A Power-class net gets 0.5 mm copper;
  previously everything was drawn at a fixed 0.20/0.25 mm. Verified:
  fixture heal outcome unchanged (3V3 is Default-class; gate 63/1 as
  v1.2.0); MOSI finish on the v1.0 pass-2 board rolls back exactly as
  the unpatched worker does (attempt-order-sensitive case, no delta).
- **Per-net heal (pipeline):** heal is invoked per split pour net
  (`worker heal --nets <net>`) and each net's bridges are gated
  separately — one net's fault no longer reverts another net's good
  bridges (v1.2 reverted all-or-nothing at the pipeline level).
- **Congestion features (worker summary):** ratsnest MST crossing
  count between different nets + RUDY peak/p95 over a 32x32 bbox
  grid — the deterministic global features the strategy-chooser
  dataset needs (in-house board: 351 crossings, RUDY peak 7.58 / p95 2.50).
- Project now versioned in the internal GitLab:
  `root/kicad-ai-router` (project 23). Runtime data dirs (auth
  token, run outputs) are gitignored.
- Still open from the v1.2 list: displacement-assisted healing for
  pocket/sliver fragments (J1.1, Q1.1); iterated from-current
  routing for tri_panel-class boards.
## v1.2 (2026-11-21) — corpus grid, chooser verdict, plane-fragment healer

**Corpus grid (regression + strategy dataset in one run).** All 59
PCBench corpus boards routed on the large backend host backend under the shipped
layer law (variant A), plus the In2-banned law (variant B) on the
four 4-layer boards: 63 jobs. Variant A fully routed **55 of 59**
boards; excluding the tri_panel outlier (84 unconnected — a routing-
capacity problem), the other 58 boards carry just 9 stranded
connections (srambo 6, DSKY 2, CH330 1 — all signal nets; every pour
net on the corpus closes). Dataset: `dataset-v1/dataset.jsonl`
(board features × strategy → gate outcome), extracted from run data
on disk. One incident on the way: the backend retention policy
(keep 20 runs) silently deleted the first ~32 grid runs' data;
keep_runs is now 500 on all live configs (code default 200) and the
pruned jobs were resubmitted — never treat job submission as data
persistence.

**Strategy chooser: measured gap is zero; shelved pending a data
decision.** Step zero of the research plan (seeded in the Papers KB:
algorithm selection beats RL at this scale) is measuring the
oracle-vs-best-fixed-strategy gap before training anything. On the
layer-law axis the gap is **0** — variant A matches or beats B on
every board where both ran, so a chooser would only learn the
current default. The other axes (first_nets ordering, pass policy)
have no dataset yet; generating one is a ~320-run expansion grid.
That is an owner decision, not an engineering default.

**Plane-fragment healer (`heal`).** New worker command + pipeline
tool + AUTO step after stitch. It rejoins split pour-net fragments
that the older tools structurally cannot see: stitch only heals
floating clusters with no vias/TH pads, and finish_leg plans
pad-to-pad with displacement — the in-house board's 3V3 fragments each carry
vias and stub tracks. Method: bridges between the nearest copper
points (pads, vias, track ends) of a minority component and the
main component, planned on the finish grid; if the fragment's pads
sit in a copper pocket on both outer layers, a **via hunt** samples
legal via sites inside the fragment's plane-layer island (its own
filled zone polygon on an inner layer) and plans from there. Every
bridge is verified twice: connectivity must improve, and an
exact-geometry check of just the new items against all foreign
copper (0.20 mm rule, keepout discs, via-crowding) must pass — a
bridge that would add a clearance fault is rejected in the worker,
not merely gate-reverted afterwards. Measured on the preserved A2
fixture (`dataset-v1/fixtures/in-house-a2-3v3-split.kicad_pcb`): of
3V3's 3 minority fragments, 2 close cleanly (gate stays at the
baseline 63 violations, zero new faults); the last fragment (J1.1)
stays open — its only legal via sites sit
inside the same B.Cu pocket and its keepout-adjacent site fails the
disc test, so it needs displacement or a manual via. Both backends'
self-tests PASS on 1.2.0, with the in-house board self-test at
**1 unconnected / 1 split** on both (best yet). PCM 1.2.0 zip
sha256 8f4eb55ac34c7eee…


## v1.1 (2026-11-21) — In2 routing, finisher v2, fault-verified finishes

Owner directive: let TraceMaker route on the 3V3 plane layer too
("allow the usage of 3rd power layer"), and optimise the algorithm
"to be able to cover the whole board". Validated change list (two
proposals were rejected on evidence: iterated from-current routing
— incremental passes box the router in, measured on K3/J+K — and
stitch profiling with no measured hotspot).

- **Layer law:** default ban is now In1.Cu only (was In1+In2). The
  3V3 plane (In2.Cu) is routable; per-run override still available.
- **finish_leg v2:** pad-cluster to pad-cluster re-bridging (up to
  4 bridges per displaced victim; v1 bridged one stub pair), bridge
  window ±6 → ±12 mm, multi-round re-planning (a net in 3+
  fragments gets one leg per fragment pair), victim pairs, and a
  per-attempt child process (repeated load/mutate cycles in one
  process corrupt pcbnew state — measured). The v1-rollback leg
  (MOSI displacing NSS) now routes.
- **Finisher safety, three measured root causes fixed:** (1) the
  grid was blind to rule areas — legs crossed the GNSS_RF_KEEPOUT
  (26 items_not_allowed in one run); rule areas are now stamped as
  hard obstacles with their DoNotAllow tracks/vias flags; (2)
  free_disc cleared via_block at endpoint pads, letting leg vias
  land inside clearance of foreign copper on every layer (one via
  pierced an In2 track: a dead short) — via_block is never cleared
  at pads now; (3) inner-layer tracks are stamped into via_block
  so F/B vias cannot pierce them.
- **Gate-verified finish (pipeline):** finish_leg self-verifies
  connectivity only, so the pipeline now gates before/after every
  finish and reverts any finish that increases the count of
  shorting_items + clearance + items_not_allowed. A short is worse
  than a split net; v1.1 finals never add copper faults.
- **AUTO:** pour-net finish before stitch; stitch runs whenever
  pour items exist (v1.0 required ALL split nets to be pour nets);
  plateau early-break (identical unconnected + failing-signal set
  twice → finishing); first_nets drops <2-pad pseudo-nets; prune
  step (dead-island removal, self-verified) after stitch.
- **Backend:** worker pool (config max_concurrent_jobs, large backend host=4);
  self-test verdict uses a tolerance band (config
  selftest_tolerance, default +2 unconnected / +0 split) — strict
  equality was the wrong test for a nondeterministic router
  (identical stack reproduced in-house board at 5/65, 5/65, 6/91).
- **Deployment gotcha (fixed in config, kept here as a warning):**
  the backend Host allowlist auto-detects only the container
  bridge IP; LAN clients were 403'd (surfacing as broken pipes on
  large uploads) until the host's LAN IP + hostname were added to
  allowed_hosts.

### Final A/B measurement (in-house board, AUTO, large backend host, independently re-gated)

| run | law | unconnected | violations | copper+keepout faults |
|---|---|---|---|---|
| v1.0 (large backend host) | In2 banned | 5 | 65 | possible (unverified finishes) |
| A2 | In2 allowed | 3 (all 3V3 plane fragments) | 63 | **0** |
| B2 | In2 banned | 2 (MISO leg, MOSI stub) | 65 | **0** |

With In2 allowed, pass 1 routes all 159 signal connections (92
segments land on the 3V3 layer; B.Cu use collapses ~270 → ~67),
but the plane itself fragments and its last fragment (the J1.1
cluster) resists clean finishing. With In2 banned the plane stays
intact and the residuals are the two hardest signal legs in the
SPI cluster. Neither law reaches 0 unconnected on this board yet;
what v1.1 guarantees is that the finishing machinery no longer
trades connectivity for shorts — every finish is gate-verified.
PCM 1.1.0 zip sha256 959e9fb87bd1cc5b…


## Field test results (2026-11-21, in-house board, dev host + large backend host)

- **AUTO mode, full run:** 4 passes with casualty promotion -- pass 4
  routed every signal net; stitch closed 4 of 5 pour islands.
  Final gate: **1 unconnected (one GND pour item at Q1.1), 61 violations**
  (Rev H hand-finished: 0/58). Q1.1 is a 0.65 mm-pitch SOT-23-6 GND pad
  in a moated sliver -- no legal 0.6/0.3 via exists inside its island at
  either planning (0.23) or exact-rule (0.205) clearance. The plugin
  reports the exact pad; a human drops that via in seconds. A
  track-bridge fallback for such pads is future work.
- **LLM mode (qwen3-coder-30b), full run:** tool-calling mechanics work
  end to end. The model followed the playbook's opening, then improvised
  (incremental passes, plane nets in first_nets -- both against advice,
  though the run still converged to 2 unconnected). Its `finish_leg`
  call on MCU_LORA_MISO was exactly right; the finisher's original
  cost-only victim choice was not (it displaced V_BAT+ and broke it).
  Fixes shipped the same night: victim scoring (signal nets first,
  wide/power nets heavily penalised) and a **self-verification rollback**
  -- after any displacement the finisher re-checks both nets'
  connectivity and refuses to save a board that got worse, reporting
  `rolled_back` instead. Verified on the same board: victim became a
  signal net, the target connected, the bridge failed, and the tool
  rolled back cleanly rather than saving damage.
- **Trust the gate printout, not the model's prose.** In the LLM test
  the model's `done` note claimed success while the gate showed
  3 unconnected / 80 violations. The dialog always displays the real
  final gate numbers; the model's note is commentary.
- One refill-patch bug (a missing function definition) was caught by
  the LLM test, not by AUTO: route now treats refill as best-effort
  and continues with a warning if it ever fails again.

## Corpus benchmark — PCBench / PCBWorld D3 boards (2026-11-21)

Corpus: PCBench collection (github.com/PCBench/PCBench, 1,182 boards) run through
LG PCBWorld's own prep chain (convert_v9 -> drc_fix_v9 -> guide -> sort) under
KiCad 10: 1,169 converted, and a size-stratified 117-board subset put through the
DRC filter yielded 59 boards (the full chain at LG's pass rate implies the same
~679-board D3 set). Benchmark sample: 24 boards stratified by pin count
(6..653 pins, 3..89 nets, 22x 2-layer + 2x 4-layer), AUTO mode, work in
`bench/`, raw results in `bench-results.jsonl` (harness: `bench.py`, `bench_one.py`).

Result: **23 of 24 boards fully routed** (0 unconnected, 0 split nets at the
gate). Across those 23 boards the final DRC reports 1,328 violations of which
exactly **one is a copper clearance violation** (kitspace_CH330) — everything
else is silkscreen/text/footprint-library classes inherited from the source
boards. Typical runtime 30-45 s per board.

The one failure is the largest board by far: kitspace_tri_panel_4up_r0.1
(653 pins, 89 nets, 157 components, 4-layer). Four passes improved steadily
(102 -> 74 -> 70 -> 62 unconnected) but plateaued; finish_leg found no legal
paths at that congestion. The fresh-pass playbook converges when the failing
set is small; at this scale it needs iterated from_current routing or a
stronger finisher. Final: 62 unconnected / 40 split nets, 18 min.

Bugs the benchmark found and fixed (all were invisible on the in-house board):
1. Worker results are now returned via a result file + `AIR_RESULT` sentinel —
   SWIG leak warnings can flood/interleave stdout on some boards and corrupt
   the old last-line JSON protocol.
2. `cmd_strip` snapshots `GetTracks()` once and pops — re-querying after a
   `Remove()` raises `TypeError: 'SwigPyObject' object is not iterable`
   inside KiCad 10's pcbnew shim.
3. `--no-tracks-on` is omitted when the ban list is empty and filtered to the
   board's actual layers — TraceMaker rejects `""` and unknown layer names.
4. Gate pour/signal classifier now handles quoted zone names.
5. `cmd_stitch` generalises over every pour net (any net with zones) instead
   of hard-coding `GND`, and is a no-op on zone-less boards. Verified
   behaviour-identical on the in-house board test board.
6. AUTO keeps the best pass by gate, not the last (passes can regress at
   scale: 72 -> 137 observed), and finish/stitch/route failures degrade to a
   final gate instead of killing the run.
7. `first_nets` is capped at 64 — TraceMaker rejects longer advice lists,
   which AUTO's failing-net promotion can exceed on many-net boards.

## in-house board regression + pour-net fix (2026-11-21, later)

Re-running AUTO on the in-house board unrouted base exposed two regressions vs the test1
result (1 unconnected / 61), both root-caused and fixed the same day:

1. **Strip refill fragmented the pours.** The refill-before-every-save patch had
   also been applied to the strip step, so TraceMaker received a base whose
   pours were fully refilled. A/B on identical advice: strip-refilled base ->
   pass 1 gate 22 unconnected (21 pour, MCU_LORA_DIO1 split, finisher no_path);
   raw base -> pass 1 gate 5 unconnected (5 pour, ZERO split nets). Fix:
   cmd_strip no longer refills (internal intermediate; refill stays at the
   gate via kicad-cli and at finalize for user-facing saves).
2. **Gate could not tell pour nets from signal nets.** split_nets excluded only
   a hard-coded 'GND', so a split 3V3 plane was treated as a failing signal
   net: AUTO promoted it into first_nets and burned passes that could never
   fix a pour island. Fix: worker summary now reports pour_nets from pcbnew
   (verified ['3V3','GND'] on the in-house board); the gate carries them; AUTO promotes
   and finish_legs only signal nets and sends pour-only splits straight to
   stitch. (A first fix attempt via a board-file regex silently never matched
   - caught by the gate reporting pour_nets [] - and was replaced by the
   worker approach.)

Full-run outcome with the pre-fix code (two independent runs - headless bench
and the owner's own GUI run from KiCad - identical): passes plateau 22 ->
finish_leg DIO1 no_path -> stitch 14 clusters (8 pads have no legal stitch
via: U4.2, U2.4, Q1.1, C12.2, R18.2, C5.2, C11.2, U3.2) -> final 7 unconnected
/ 65 violations. Also noted: stitch costs ~11 min CPU on this board (profile
in v1.1); the benchmark harness probe had its own bug (layer names vs
canonical names on renamed-layer boards) - fixed positionally.

## Packaging build — PCM + Docker backend (2026-11-21)

Implements PACKAGING-PLAN.md (cold-reviewed, filed in Paperless).

- **Backend service** (`docker/server.py`, stdlib only): async job queue
  (concurrency 1, subprocess per job), `GET /health` handshake (backend
  version, kicad-cli version, TraceMaker versions, config schema),
  `POST/GET /api/runs`, cancel, result download, submit dedupe,
  retention pruning, interrupted-state recovery on restart.
- **Security (v1, per the review)**: bearer token generated at first
  start + Host-header allowlist. Verified live: no-auth -> 401,
  spoofed Host -> 403, API key never returned by config reads.
- **Config page** (`docker/config_page.html`, served by the backend):
  LLM endpoint/model, write-only API key with an explicit clear control,
  routing defaults, TraceMaker manager, self-test panel, job list.
  One JSON config on the bind-mounted data dir; atomic writes;
  `schema_version` with backup-on-migrate.
- **TraceMaker on the volume**: versioned folders, bootstrap/update from
  a configured URL (compose env `TRACEMAKER_URL`), smoke test
  (`tracemaker version`), TOFU sha256 recorded per version and enforced,
  per-run resolution to the versioned absolute path, recorded in the
  job record. Verified host + container: identical sha256
  `8e8efd53…` for the 0.12.1 test archive.
- **Image** (`docker/Dockerfile`): Ubuntu 24.04, KiCad pinned
  `10.0.8~ubuntu24.04.1` from the 10.0 PPA (footprints + symbols, no
  3D/demos/docs), python3-numpy (worker.py needs it — first container
  run caught its absence; host runs never noticed). Image size 1.98 GB.
  Compose (`docker/docker-compose.yaml`): localhost bind, TZ, CPU cap,
  `../data` bind mount.
- **Self-test**: in-house board base + hbridge-driver + mapone corpus boards
  baked into the image (`docker/selftest/`); baselines recorded to the
  data volume; the config page shows PASS / DRIFT per board.
- **Plugin**: backend attach mode added alongside local mode (checkbox,
  URL, token; health readout; submit/poll/download; result saved as
  `<board>-backend.kicad_pcb`; "Backend config page" button). Local
  mode untouched — the keep/delete-fallback decision is still open.
- **PCM**: `build_package.py` produces `pcm/ai-router-1.0.0.zip`
  (metadata with no `download_*` keys, 134-char description) and
  regenerates `pcm-repo/` (packages.json + repository.json + zip) on
  every build. Simulated PCM install into the 3rdparty layout imports
  and registers identically to the scripting install. PCM acceptance
  of the plain-http LAN repo URL is still unverified against a real
  PCM client.

### Results + bug 8 (2026-11-21, later)

- Native backend, in-house board job over HTTP: pass 1 gate 5/0, final **1
  unconnected (pour) / 0 split / 62 violations**; the downloaded result
  board re-gated independently with kicad-cli: identical numbers.
- Container backend, in-house board job: passes 9 -> 5 -> 9 -> 9 (variant luck),
  best-pass retention restored the 5, finish_leg MOSI rolled back by
  its own verification; final **5 unconnected / 2 split**.
- **Bug 8 — the layer ban broke every board without inner layers.**
  The backend always passes the configured `--no-tracks-on
  In1.Cu,In2.Cu`; TraceMaker rejects ban names that are not copper
  layers of the board (`error: --no-tracks-on: "In1.Cu" is not a
  copper layer of this board (F.Cu, B.Cu)`), so 2-layer boards failed
  every route pass and AUTO finished from the stripped base (hbridge
  57 unconnected, mapone 85). The pipeline's layer filter never
  engaged: the worker summary returned no layer list at all (the host
  bench never noticed — its harness computed the ban per board).
  Fix: the worker summary now returns `copper_layers` (the canonical
  stack from `GetCopperLayerCount()` — correct even when a board
  renames its inner layers, e.g. the in-house board's GND/3V3), and
  `tool_route` intersects the configured ban with it. Verified:
  hbridge and mapone now route **0 / 0** in the container, matching
  the host bench exactly; the in-house board keeps its full ban.
- Container passes are slower than host passes (~15 min vs ~4-10 for
  the in-house board under the 3-CPU cap, TraceMaker variant racing).
- Self-test baselines recorded from the fixed stack (2026-11-21):
  hbridge {0 unconnected, 0 split}, mapone {0, 0}, in-house board {5, 2}.
  Verification re-run: both corpus boards reproduced 0/0 (PASS).
  Verification re-run verdicts: hbridge PASS, mapone PASS, in-house board
  DRIFT — re-run landed 6 unconnected / 2 split / 91 violations vs
  the {5, 2} baseline on a byte-identical stack. That is the board's
  known run-to-run variance (three identical-stack container runs
  today: 5/65, 5/65, 6/91; casualty rotation in the SPI cluster),
  not a regression: the detector flags movement, a human judges it.
  A tolerance band for variance-prone baselines is a v1.1 candidate.

### large backend host deployment (2026-11-21)

The same image now also runs on the large backend host G9 (backend host):
`~/workspace/ai-router/` (root compose file, data in-folder, LAN
port 8731, `cpus: 64`), a config twin of the dev host instance
(same token, same TraceMaker 0.12.1 TOFU record).

- **Deployment gotcha:** the Host allowlist cannot see the host's
  LAN IP from inside a container (it detects the bridge IP), so a
  LAN client was rejected (403, surfacing as a broken pipe on large
  uploads) until `backend host` was added to `allowed_hosts` in
  the large backend host copy's config. Any future LAN deployment needs its
  host IP (or name) in `allowed_hosts`.
- in-house board AUTO job over the LAN: **5 unconnected / 2 split / 65** —
  identical final to the dev host container run, in ~21 min total
  (~5-7 min/pass) vs ~15 min/pass under the dev host's 3-CPU cap.
  TraceMaker's portfolio is 8 threads regardless of the cap, so
  extra cores buy an unthrottled variant race (~3x here), not
  linear scaling; the remaining headroom would suit parallel jobs
  (the queue is serial today).

