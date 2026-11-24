# KiCad AI Router

Developed by [SiliconBrane Inc](https://www.siliconbrane.com).

![version](https://img.shields.io/badge/version-v1.10.1-blue) ![license](https://img.shields.io/badge/license-MIT-green)

A KiCad 10 plugin + routing backend that finishes PCB layouts with
deterministic tools under an exact-geometry gate -- and, optionally, an LLM
that makes **strategy decisions only, never geometry**.

Every experiment behind this project said the same thing: language models
are bad at coordinates and good at choosing what to try next. So the LLM
(or the built-in AUTO strategy, which needs no LLM at all) picks tools and
orderings; all copper is drawn by deterministic code, and every result is
accepted or reverted by `kicad-cli` DRC plus exact clearance checks. The
gate's verdict is the only success criterion -- never the model's prose.

## What it does

- **Full-board routing** via [TraceMaker](https://github.com/DingoOz/TraceMaker)
  passes with configurable net ordering and layer law (e.g. never route on a
  solid GND plane; pour planes keep priority on their own layer).
- **A repair toolkit** for what the main pass strands: a grid-A* finisher
  with multi-victim displacement and re-bridging, a large-window finisher
  for far-apart fragments, a plane-fragment healer, and a pour stitcher --
  each one gate-verified as a unit, so a repair that adds faults is reverted
  even if its connectivity looks better.
- **Honest metrics**: per-layer track census (DRC cannot see a track drawn
  across a power plane -- the census can), split-net counts for signal and
  pour nets separately, via count and wirelength on every gate.
- **Two ways to run**: inside KiCad as an ActionPlugin talking to the
  backend over HTTP, or headless -- the backend is a small Docker service
  with a job API, self-tests, and a status page.

## Measured results

- PCBench corpus (59 boards): the pipeline fully routes most boards it is
  given; on a 24-board sample, 23 finished fully routed with exactly one
  copper clearance violation across all 23.
- dataset-v3 (139 hard boards harvested from public designs, 432 measured
  backend runs): on boards the pipeline completes cleanly, median
  wirelength is **0.83x the human original's** at 1.0-1.1x the vias.
  Net-ordering policy made no measurable difference; layer law did.
- The repair toolkit closes the "far-apart fragment" class completely
  (fragments 100+ mm apart). Congestion-bound residuals remain open --
  three successive repair strategies (ILP candidate selection, victim
  re-bridge upgrades, attempt selection) and a PathFinder-style
  negotiation prototype all measured zero against them, and the
  [changelog](CHANGELOG.md) shows the receipts. This tool is a strong
  finisher, not a magic router.

## Requirements

- KiCad 10 (plugin and `kicad-cli`).
- TraceMaker 0.12.1 for full-board passes (the finisher/healer tools work
  without it). Place the binary in the backend data dir under
  `tracemaker/0.12.1/`; see `docker/` for the layout. The backend refuses
  unverified downloads -- TraceMaker is distributed locally by design.
- For LLM mode: any OpenAI-compatible endpoint. AUTO mode needs none.
- Docker for the backend, or run `docker/server.py` on any host with
  KiCad 10 + Python 3.

## Install

**Plugin (PCM):** add the repository index from `pcm-repo/repository.json`
in this repo (host it anywhere static), or unzip `pcm/ai-router-1.7.0.zip`
into your KiCad scripting plugins folder. Configure the backend URL and
token in `~/.config/ai_router/config.json`.

**Backend:**

```sh
cd docker && docker build -f Dockerfile -t ai-router-backend ..
docker run -d -p 8731:8731 -v $PWD/data:/data ai-router-backend
```

On first start the backend generates a config with an auth token under
`data/config/`; the built-in self-tests (two public sample boards) run
from the status page or `POST /api/selftest/run`.

## Layout

- `ai_router/` -- the plugin: `worker.py` (all geometry tools, run as
  subprocesses under KiCad's Python), `pipeline.py` (gate-verified tool
  wrappers), `orchestrator.py` (AUTO strategy + LLM playbook).
- `docker/` -- backend service (`server.py`, `run_job.py`), Dockerfile,
  self-test boards.
- `dataset-v1/ v2/ v3/` -- the benchmark corpora, grid runners and
  extracted results behind the numbers above.
- `CHANGELOG.md` -- the full engineering record, version by version,
  including the approaches that did not work.

## License

MIT (see `LICENSE`). Sample boards in `docker/selftest/` are public
open-hardware designs, included for testing; their original licenses
apply.
