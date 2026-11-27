# Bench harness (#62)

The standing measurement tool for the KiCad AI Router pipeline:
prepared-input caching, reproducible single-stack runs, A/B
comparison, and a nothing-worse gate scored on **routing-caused
DRC**. It generalises the ad-hoc Phase 2 adoption A/B machinery
(`agent-runs/phase2-compare/`) and discharges issue #54's remainder
(the PCBWorld-Bench harness). Issue #62 is the requirements source.

## Why it exists

Two measurement-integrity risks:

1. **Input drift.** If prepared (stripped) inputs are regenerated
   per run, input generation differences masquerade as routing
   differences. The harness caches prepared inputs keyed by a hash
   of the source board files; every later run routes the identical
   prepared input, and the prepared file's sha256 is re-verified on
   every cache hit (a mismatch is a hard error).
2. **Raw-count gates.** A nothing-worse gate charged on raw DRC
   counts bills the router for faults the board already had. Here
   "worse" is defined per board, per violation kind, as **KiCad DRC
   after routing minus KiCad DRC of the stripped input** (unconnected
   items tracked as their own kind). Estimator-vs-KiCad drift (the
   pipeline gate's counts vs KiCad DRC's) is recorded per board and
   reported, never folded into the gate.

## The exam guard

`dataset-v3/split.json` records 90 train and 49 held-out boards. The
49 held-out boards are the final exam's: **normal operation refuses
them** (`HeldOutError`, CLI exit 4). The only path is `--exam`,
which exists so the final exam is an invocation of this harness, not
new code; exam runs are labelled `"exam": true` in their manifest.
Do not tune against held-out boards.

## Data layout

Data root (`--home`, env `BENCH_HARNESS_HOME`, default
`~/Documents/workspace/agent-runs/bench-harness`):

```
cache/<source-key>/   prepared.kicad_pcb (+ .kicad_pro/.kicad_dru),
                      source copies, prep.json (hashes)
runs/<run-id>/        manifest.json  (stack, config fingerprint,
                                       git SHA, plugin version,
                                       engine path + sha256, per-board
                                       source/prepared hashes)
                      summary.json / summary.md
                      boards/<slug>/ record.json, run.log,
                                       board.kicad_pcb (staged input),
                                       result.kicad_pcb, ledger.json,
                                       input.drc.json, output.drc.json,
                                       ai-route-*/ (pipeline workdir)
```

## Commands

```
python3 -m bench_harness inventory [--fixtures-dir DIR]
python3 -m bench_harness prep    --slugs a,b
python3 -m bench_harness run     --stack current --slugs a,b \
                                 [--cfg-json '{"tm_work": 40000000}'] \
                                 [--run-id ID]
python3 -m bench_harness ramp    --baseline RUN_DIR --split train
python3 -m bench_harness compare RUN_A RUN_B [--gate] [--out table.md]
```

Selection: `--fixtures-dir` (repeatable) adds fixture boards;
`--split train|held_out|all` picks the dataset split (default
`train`, or fixtures-only when only `--fixtures-dir` is given);
`--slugs` narrows to named boards. Board files resolve from
`--boards-root` (env `DSV3_BOARDS`, default
`~/Documents/workspace/agent-runs/dataset-v3-boards`).

A **stack** is a name plus pipeline-config overrides; the manifest
records the effective config's fingerprint and the resolved engine
binary's sha256, so two runs are comparable only when their
manifests say what differed. Runs are resumable: re-invoking a run
with the same run-id skips boards whose record is complete for the
same prepared input and config fingerprint. Long sets should be
launched detached (`setsid nohup python3 -m bench_harness run ...`)
and inspected through the run directory while they work.

**Ramp**: boards are grouped by stratum and run tier by tier
(`--tier-order`, default A,B,C); after each tier the tier's records
are gated against the baseline run with the nothing-worse rule and
the ramp stops at the first regressing tier (exit 3).

**Compare / gate**: `compare` renders the A/B table (per-board gate
triples {unconnected, splits, copper} for both stacks, caused-DRC
totals, wall times, aggregate). With `--gate`, B is the candidate
and A the baseline: exit 3 if B regresses anywhere -- any
routing-caused kind above A's caused value on that board, or any
gate-triple component worse.

## Record fields (per board)

`gate` (pipeline gate triple), `gate_raw` (full gate record),
`input_drc` / `output_drc` (KiCad DRC summaries: by-kind counts,
total, unconnected), `caused` (per-kind output-minus-input deltas),
`drift` (pipeline-gate vs KiCad counts, report-only), `wall_s`,
`workdir`, `ledger`, `status`, plus the source/prepared hashes and
the config fingerprint.
