# Copyright (C) 2026 SiliconBrane Inc.
#
# This file is part of KiCad AI Router.
#
# KiCad AI Router is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KiCad AI Router is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KiCad AI Router. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Configuration for the AI Router plugin. Persisted as JSON in ~/.config/ai_router/config.json"""
import json, os

CONFIG_PATH = os.path.expanduser('~/.config/ai_router/config.json')

DEFAULTS = {
    'endpoint': 'http://127.0.0.1:8080/v1',
    'model': 'qwen3-coder-30b',
    'api_key': '',
    'tracemaker_path': os.path.expanduser('~/Documents/workspace/tracemaker-test-2026-11-12/tracemaker/tracemaker-0.12.1-linux-x86_64/bin/tracemaker'),
    'kicad_cli': 'kicad-cli',
    'mode': 'auto',              # v1.9 (M16): 'auto' is the measured, published path -- every dataset and self-test number comes from it; 'llm' stays available as an explicit choice
    'max_passes': 4,
    'finish_signal_cap': 8,   # AUTO: max signal finish_leg attempts per run (v1.2.3; was hard-coded 4)
    'finish_pour_cap': 4,
    'long_finish_cap': 6,
    'pass_advice': 'promote',  # v1.9 (Phase 1.3): pass-to-pass advice scheme -- 'promote' = failing signal nets join first_nets (protected, pre-v1.9 behaviour); 'priority' = first_nets stays the initial critical set and failing connections are ordered via the advice 'priority' pairs (unprotected), built from the previous pass's stuck report
    'advice_regions': False,
    'reroute_window_cap': 0,   # v1.9 (Phase 1.5): windowed engine re-route attempts on split signal nets after the post-prune pass (0 = off)
    'reroute_accept': 'connectivity',  # v1.9 (Phase 1.5): reroute acceptance key -- 'faults' = the step key (faults first); 'connectivity' = unconnected/splits first, faults last. v1.9.5: default flipped to 'connectivity' -- in the integration A/B the 'faults' key accepted zero reroutes in 12 jobs (an expensive no-op) while 'connectivity' produced the only win (bitaxe 14->10 unconnected)
    'engine_portfolio': 'tm_relax',  # v1.9.5 (Phase 1.2): comma list of alternative engines run from the base and gate-selected (freerouting, krt, tm_relax, tm_global, tm_escape); '' = TraceMaker only. Default 'tm_relax' per the 7-board A/B: tm_relax won the candidate selection on all 7 boards and the portfolio arm never lost a final gate; freerouting/krt stay opt-in (never selected; krt connects nearly everything but at 430-1142 copper faults)
    'portfolio_workers': 4,  # Phase 2E (#66): portfolio racing heat size -- how many candidate legs run at once. Selection is fixed-order (first complete in the list wins; best acceptance key otherwise), so the outcome never depends on this number; 1 = sequential in-process legs.
    'reroute_margin': 6.0,     # v1.9 (Phase 1.5): window margin (mm) around the stuck corridor / net bbox   # v1.9 (Phase 1.3): with pass_advice='priority', also reserve the stuck report's per-connection region_mm corridor for its net (advice 'regions', reserve_for)
    'attempt_select': False,  # v1.7: bridge-aware attempt selection in finish_leg -- DEFAULT OFF: measured 2026-11-22 head-to-head vs v1.6.0 on srambo (6 nets) + Manikin (4 nets): outcomes identical on all 10, 0 closures, probes cost +14-39s where they engage; enable for the attempts_probed diagnostics
    'ilp_repair_cap': 0,      # AUTO: joint ILP repair per run (v1.5, phase (c) Stage B) -- DEFAULT OFF: measured 2026-11-22, the ILP closes nothing yet (execution/re-bridge binds, not selection); enable per-run to experiment     # AUTO: max large-window hard-A* long_finish attempts per run (v1.4, phase (c) Stage B-0)     # AUTO: max pour-net finish_leg attempts per run (was hard-coded 2)
    'severance_scoring': False,  # Phase 2E (#64): barrier-closure (severance) penalty in ilp_repair candidate pricing -- DEFAULT OFF pending the item-4 A/B verdict
    'regret_order': False,  # Phase 2E (#65): regret-order commit sequencing in ilp_repair, replacing the ILP/greedy selection -- DEFAULT OFF pending the item-4 A/B verdict
    'work': 40000000,  # v1.12.2 (#180): recalibrated -- 20M starved Nomad-class boards on the Phase 2 engine (Stage C: 1 unconnected, stopped_by=work; the 40M rerun restored 0/0/14, job 0d50e2a7)
    'no_tracks_on': 'In1.Cu',
    'layer_law_policy': 'auto',  # v1.8: B-first + escalate on >=4L In2-pour boards (dataset-v3 gap)
    'layer_cost': '',  # v1.11 (#73): graded layer pricing, engine syntax 'In1.Cu=10,In2.Cu=10' (1 = normal, max 1000; fractional factors to two decimal places since v1.11.5, #121); '' = off. A costed layer is NOT banned -- it leaves the effective no_tracks_on set, and an explicit spec disables layer_law_policy 'auto' escalation.
    'fix_violations': True,  # v1.12 (#155, P2.1b): after reroute_window, repair router-added copper faults on connected nets (base-diff, rip +-3 mm, exact-checked re-close, gate-verified).
    'pour_skeleton': True,  # v1.12 (#63): reserve each pour's pad spanning tree in the finisher grids; the gate counts foreign copper inside a reserved corridor as a router-added fault. Engine passes stay unconstrained -- the post-route gate is the catch.
    'strip_existing': True,
    'deterministic': False,  # v1.12.2 (#171): Reproducible run -- engine --deterministic, bounded by the work budget alone (no --time is passed)
    'route_open_only': False,  # v1.12.2 (#171): Finish this board -- engine --route-open-only; forces strip_existing off (the existing copper is the point)
    'max_tool_calls': 48,
    'request_timeout_s': 600,
    'use_backend': False,
    'backend_url': 'http://127.0.0.1:8731',
    'backend_token': '',
    'backend_servers': [],  # (#199): custom backend servers added from the dialog's Run on group -- [{'name', 'url', 'token'}]; the built-in presets stay in dialog.BACKEND_PRESETS
}

def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    # (#196): a config saved before #180's recalibration persists
    # the old 20M default verbatim; migrate that exact value to
    # the 40M default (persisted on the next save). Any other
    # value is the user's own setting and is left alone.
    if cfg.get('work') == 20000000:
        cfg['work'] = 40000000
    return cfg

def save(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)
