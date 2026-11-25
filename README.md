# KiCad AI Router

Developed by [SiliconBrane Inc](https://www.siliconbrane.com)

![version](https://img.shields.io/badge/version-v1.11.5-blue)
![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

KiCad AI Router is a routing and routing-repair system for KiCad 10.

The project started from a simple observation after a lot of experiments: large language models are terrible at geometry, but surprisingly useful for deciding what to try next.

Instead of letting an AI draw copper directly, KiCad AI Router uses deterministic routing tools that generate all board geometry. The AI, when enabled, only chooses strategies and tool ordering. Every change is verified before it is accepted.

If a routing step introduces new violations, it gets reverted.

The goal is not to replace PCB designers or compete with professional routing workflows. The goal is to automate as much of the repetitive routing work as possible while producing results that survive real KiCad verification.

---

## How it works

The system is built around a simple rule:

> The router decides. The gate judges.

Routing tools generate candidate changes. Those changes are then checked using:

- `kicad-cli` DRC
- Exact clearance validation
- Per-layer track accounting
- Connectivity checks
- Split-net analysis

A change is considered successful only if the gate accepts it.

Not because an LLM says it looks good.
Not because connectivity improved.
Not because a heuristic thinks it is better.

Only the gate's result matters.

---

## What it can do

### Multi-engine portfolio and full-board passes

Full-board routing runs in `AUTO` mode (the default, measured path):

- **Engine portfolio (`engine_portfolio`):** Before standard passes, candidate routes from stripped base can run across TraceMaker variants (`tm_relax` enabled by default; `tm_global`, `tm_escape` opt-in) as well as bundled **Freerouting 2.5** and **KiCadRoutingTools (KRT)** in the Docker backend. Candidates are judged faults-first by the gate so high-violation routes never win on connectivity alone.
- **Layer law and graded layer pricing (`no_tracks_on`, `layer_cost`, `layer_law_policy`):** Inner reference/power planes can be hard-banned (`no_tracks_on`), automatically escalated from plane-protected to open when signal nets stay split (`layer_law_policy=auto`), or priced per cell (`layer_cost`, e.g. `In1.Cu=10,In2.Cu=10`) so inner layers are used only when outer detours are worse.
- **Iterative triage passes:** Up to `max_passes` TraceMaker passes promote failing signal nets (or priority pairs/corridors) and retain the best pass on connectivity, split count, DRC faults, and cost-weighted wirelength.

### Post-pass routing repair

When full-board passes leave split nets or isolated pour islands, deterministic repair stages run in sequence:

- **Local and board-scale finishing (`finish_leg`, `long_finish`, `ilp_repair`):** 3D grid A* finishing (respecting configured bans and layer costs), single- and cut-set victim displacement with re-bridging, and large-window hard-A* paths for distant fragments.
- **Pour stitching and plane healing (`stitch`, `heal`, `prune`):** Via stitching and short track bridges for floating pour pads, additive bridges (with island via-hunting) for split power/ground planes, dead-copper pruning, and a post-prune pour pass.
- **Windowed engine re-route (`reroute_window`, opt-in):** Locks copper outside a stuck connection's corridor (preserving user-locked tracks by UUID), lets TraceMaker `--reroute` rip and re-route the window, and accepts the trade under a configurable key (`connectivity` or `faults`).

Finisher, stitch, and heal additions are checked before commit against exact clearance, keepout, drill (`hole_clearance` / `hole_to_hole`), and `Edge.Cuts` geometry, and every pipeline step is gated by `kicad-cli` DRC plus the banned-layer track census (reverting steps that degrade the stage's acceptance key).

### AUTO mode vs. LLM mode

- **AUTO mode (default):** Deterministic strategy and repair sequence, no LLM required. All published benchmarks and backend self-tests run in `AUTO` mode.
- **LLM mode (experimental):** Any OpenAI-compatible endpoint drives the `v1.8` tool loop (`route`, `gate`, `finish_leg`, `long_finish`, `ilp_repair`, `stitch_gnd`, `heal`, `prune`) without emitting geometry. Newer `v1.9+` stages (`portfolio`, `reroute_window`, `layer_law_policy`) run in `AUTO` mode only; on endpoint error, `LLM` mode falls back to `AUTO`.

### Automated tests and self-tests

- Unit and pcbnew fixture tests in `tests/`, run in GitHub Actions CI inside the KiCad 10 backend image on every push (skipping `pcbnew`-dependent fixtures cleanly outside KiCad).
- Built-in end-to-end backend self-tests (`docker/selftest/`, triggered via the web UI or `POST /api/selftest/run`).

---

## Results

All measurements below were run in deterministic `AUTO` mode (`strip_existing=true`) and verified with `kicad-cli` DRC plus the pipeline's connectivity and banned-layer census.

### Early PCBench sample (`v1.6`–`v1.7`, pre-portfolio baseline)

- 24-board development sample drawn from the 59-board PCBench corpus (`AUTO` mode, default `In1.Cu` ban): **23 of 24** boards routed to 0 unconnected items, with 1 router-added copper-clearance violation across the 23 completed boards.

### `dataset-v3` corpus (`v1.8`, layer-law ablation)

- **139** public KiCad boards (`dataset-v3/` manifests and submission drivers committed in-repo; **432** backend runs across policy arms).
- On completed boards: median wirelength **0.83×** the original human-routed board; via count typically **1.0×–1.1×**.
- Key finding: net-ordering heuristics (`first_nets_policy`, `pass_advice`) had negligible effect, whereas layer-law selection (`layer_law_policy=auto`, Variant B-first with Variant A escalation on ≥4-layer pour boards) closed most of the 44-connection single-policy vs. oracle gap on the 54 multilayer boards.

### Tiered benchmark & `P2.0` fault census (`v1.9.5`–`v1.9.6`)

- Decomposing **60** production run ledgers at three gates (`base` → `routed` → `final`) showed **0 net router-added copper DRC violations** on completed bench boards — the raw copper violations (416 across 43 boards, led by `solder_mask_bridge` and `copper_edge_clearance`) were already present in the unrouted input placements.
- Scored against each board's own stripped input (`CP_delta`: 0 unconnected, 0 splits, and no increase in DRC faults over base), completion stands at **1.00 (easy)**, **1.00 (medium)**, and **0.90 (hard)**. *(The standalone tier-sweep harness with hash-cached prepared inputs is tracked for commit under `#62`.)*

### Known limitation

Congestion remains the hardest problem.

Several approaches were tested, including:

- ILP candidate selection
- Improved victim re-bridging
- Attempt ranking
- PathFinder-style negotiation routing

None showed measurable improvement on the remaining congestion-bound failures.

The project is a strong routing finisher and routing-repair system.

It is not a magic autorouter.

---

## Requirements

### Required

- KiCad 10
- Python 3
- `kicad-cli`

### For full-board routing

- TraceMaker 0.12.1

Place the binary in:

```
tracemaker/0.12.1/
```

inside the backend data directory.

The backend fetches TraceMaker from the configured URL (`TRACEMAKER_URL` or the `tracemaker.url` config) -- on first startup and via its update endpoint -- and installs it only after a sha256 check against the recorded hash for that version (first use of a new version is trust-on-first-use). A local file path or `file://` URL can be configured instead of a download.

### For AI mode

Any OpenAI-compatible API endpoint.

AUTO mode does not require an LLM.

### Backend

Either:

- Docker (recommended)
- A host machine with KiCad 10 and Python 3 capable of running `docker/server.py`

---

## Installation

### Installing the KiCad plugin

Add the repository to KiCad's Plugin and Content Manager
(Preferences -> Plugin and Content Manager -> Manage Repositories)
by URL:

```
https://raw.githubusercontent.com/kaushikray2/kicad-ai-router/main/pcm-repo/repository.json
```

or download the package zip from this repository and install it
from the Plugin and Content Manager via "Install from File...":

```
pcm-repo/ai-router-1.11.5.zip
```

Configure the backend connection in:

```
~/.config/ai_router/config.json
```

---

### Starting the backend

```bash
cd docker
docker build -f Dockerfile -t ai-router-backend ..
docker run -d -p 127.0.0.1:8731:8731 -v $PWD/data:/data ai-router-backend
```

On first startup the backend creates:

- Configuration files
- Authentication token
- Runtime directories

The backend also includes built-in self-tests using public sample boards.

Tests can be run through:

- The status page
- `POST /api/selftest/run`

---

## Repository layout

```text
ai_router/
├── worker.py
├── pipeline.py
├── orchestrator.py

docker/
├── server.py
├── run_job.py
├── selftest/

dataset-v1/
dataset-v2/
dataset-v3/

CHANGELOG.md
```

### Important files

**worker.py**

Contains the geometry tools that execute under KiCad's Python environment.

**pipeline.py**

Wraps routing and repair tools with gate validation.

**orchestrator.py**

Implements AUTO mode and LLM-assisted strategy selection.

**dataset-v1 / v2 / v3**

Benchmark datasets, runners and result archives used during development.

**CHANGELOG.md**

The full engineering history of the project, including failed experiments, discarded ideas and the data that led to design decisions.

---

## Philosophy

Most AI routing projects start by asking:

> Can an LLM route a PCB?

This project asks a different question:

> Can a deterministic router become better if an LLM only decides what to try next?

Everything in this repository follows that idea.

The AI is optional.

The geometry is deterministic.

Verification is mandatory.

---

## Appendix: design notes from the development record

*(Moved verbatim from CHANGELOG.md on 2026-11-25, #149 D10 -- the changelog keeps versions and dated records only.)*


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


## License

GPL-3.0-or-later.

Copyright (c) 2026 SiliconBrane Inc.

The sample boards included in `docker/selftest/` are public open-hardware designs and remain covered by their respective licenses.
