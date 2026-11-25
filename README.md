# KiCad AI Router

Developed by [SiliconBrane Inc](https://www.siliconbrane.com)

![version](https://img.shields.io/badge/version-v1.11.3-blue)
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

### Full-board routing

For complete routing passes the system uses
[TraceMaker](https://github.com/DingoOz/TraceMaker) together with a set of routing policies.

Examples include:

- Custom net ordering
- Layer restrictions
- Protecting solid plane layers
- Keeping pours on preferred layers
- Different routing strategies depending on board topology

### Routing repair

No autorouter finishes everything.

When routing passes leave behind disconnected nets or broken geometry, the repair pipeline takes over.

Current repair tools include:

- Grid A* finishing
- Multi-victim displacement
- Re-bridging
- Large-window fragment repair
- Plane fragment healing
- Pour stitching

Every repair operation is verified independently. If a repair fixes one problem but creates another, it is rolled back automatically.

### Measurement and verification

Every run produces metrics including:

- Signal split nets
- Pour split nets
- Via count
- Total wire length
- Per-layer track census

The layer census exists because DRC alone can miss certain situations, such as tracks crossing areas that should remain exclusive to power planes.

### AI mode or non-AI mode

The project can run in two ways:

#### AUTO mode

No LLM required.

The built-in strategy engine chooses routing actions using deterministic logic.

#### LLM mode

Any OpenAI-compatible endpoint can be used.

The model never generates geometry.

It only decides which routing tools should be attempted and in what order.

---

## Results

The numbers below come from real measured runs.

### PCBench corpus

- 59-board corpus
- On a 24-board sample, 23 boards finished fully routed
- Only a single copper-clearance violation was observed across those 23 completed boards

### dataset-v3

- 139 difficult public boards
- 432 measured backend runs

For boards that completed successfully:

- Median wire length was **0.83×** the original human-routed design
- Via count was typically **1.0× to 1.1×** the original design

One of the more surprising findings was that net-ordering strategies had little measurable impact, while layer-law policies had a significant effect on final results.

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

The backend intentionally requires a locally supplied TraceMaker binary rather than downloading one automatically.

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

Add the repository from:

```
pcm-repo/repository.json
```

to KiCad's Plugin and Content Manager, or manually install:

```
pcm/ai-router-1.7.0.zip
```

into the KiCad scripting plugins directory.

Configure the backend connection in:

```
~/.config/ai_router/config.json
```

---

### Starting the backend

```bash
cd docker
docker build -f Dockerfile -t ai-router-backend ..
docker run -d -p 8731:8731 -v $PWD/data:/data ai-router-backend
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

## License

GPL-3.0-or-later.

Copyright (c) 2026 SiliconBrane Inc.

The sample boards included in `docker/selftest/` are public open-hardware designs and remain covered by their respective licenses.
