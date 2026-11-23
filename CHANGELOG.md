# Changelog -- development record

This file is the project's engineering log, kept version by version as the
work was measured. It is candid about dead ends (LLM geometry routing, ILP
candidate selection, negotiated congestion) because the negative results are
the reason the shipped design looks the way it does. References to "the
in-house board" are to a 4-layer board developed alongside the router; its
design files are not part of this repository. Corpus boards (PCBench,
open-schematics) are named as published by their authors.

# AI Router — KiCad plugin

Routes a board from inside KiCad using the measured hybrid procedure:
an LLM (any OpenAI-compatible endpoint) makes **strategy** decisions only,
while deterministic tools do all geometry — TraceMaker for full-board
passes, a scripted grid-A* finisher for stuck legs, a connectivity-probe
stitcher for GND pour islands, and `kicad-cli` DRC as the only gate.

This is the pipeline validated by hand on the in-house board (Rev K → K2 → K3
→ in-place finish: 0 unconnected), packaged as a plugin. The LLM never
emits coordinates or traces; every effective experiment showed that path
is a dead end, and every fix moved the design back toward this split.

## Install

Copy the `ai_router/` folder into KiCad's scripting plugins directory:

    ~/.local/share/kicad/10.0/scripting/plugins/ai_router/

Restart KiCad (or refresh external plugins). The plugin appears as
**AI Router (LLM orchestrator)** with a toolbar button.

## Use

1. Open the board in Pcbnew, save it, launch the plugin.
2. Configure: endpoint, model, API key (if the endpoint needs one),
   TraceMaker binary path, mode, max passes, banned layers.
   Defaults point at the home large backend host llama.cpp server
   (`http://backend host:8080/v1`, `qwen3-coder-30b`).
3. Run. The plugin works on **copies** in an `ai-route-<timestamp>/`
   folder next to your board — your open board is never modified.
   A `ledger.json` in that folder records every step.
4. When it finishes, "Open result board" loads `final.kicad_pcb`.

## Modes

- **llm** — the model drives the tool loop under a written playbook
  (orient → route with protected first-nets → gate → promote casualties →
  finish stuck legs in place → stitch pour islands → done). If the
  endpoint errors, the run falls back to AUTO for the remainder.
- **auto** — the same playbook as deterministic rules, no model needed.

Endpoints without native tool calling are handled by a JSON-action
fallback parser (the model answers with `{"tool": ..., "args": ...}`).

## Tools the orchestrator can call

| tool | what it does |
|---|---|
| `board_summary` | nets, pad counts, split nets via KiCad connectivity |
| `scorecard` | worst decoupling caps by distance to nearest same-net pad |
| `route` | one TraceMaker pass (layer law enforced by `--no-tracks-on`) |
| `gate` | `kicad-cli pcb drc --refill-zones`; unconnected decomposed into pour vs signal |
| `finish_leg` | grid-A* finisher for one net; single-net displacement + re-bridge when blocked; planner clearance = board rule + margin |
| `stitch_gnd` | probes every GND pad's connectivity cluster; one stitch via per floating island, verified by re-probe |
| `done` | end the run |

## Rules baked in (measured, not guessed)

- **Refill before every save.** Zone fills are stored polygons; any layout
  change leaves them stale and the GUI shows pours flowing over new
  traces. Strip, every route pass, and finalize all refill first.
- **Layer law by construction:** In1.Cu (GND plane) is never routed;
  In2.Cu (power plane) banned by default (configurable).
- **First-net protection does not bind between first nets** — a protected
  net can still be ripped by another protected net; that is what
  `finish_leg` exists for.
- **Pour items ≠ signal failures.** Judge a pass by split signal nets;
  pour fragmentation is closed at the end by stitching.
- The gate (KiCad DRC after refill) is the only verdict — never the
  router's own counts.

## Honest limits

- TraceMaker is a heuristic search: identical inputs vary run to run
  (measured 5–9 unconnected across equivalent passes). The triage loop +
  finisher absorb that variance; a single pass guarantees nothing.
- A 30B local model's judgment is the weak link. The deterministic tools
  carry correctness; AUTO mode is the safety net and the baseline.
- `finish_leg` handles one missing leg of one net per call and displaces
  at most one net. Boards needing multi-net surgery still need a human.

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
