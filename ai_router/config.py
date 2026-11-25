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
    'reroute_margin': 6.0,     # v1.9 (Phase 1.5): window margin (mm) around the stuck corridor / net bbox   # v1.9 (Phase 1.3): with pass_advice='priority', also reserve the stuck report's per-connection region_mm corridor for its net (advice 'regions', reserve_for)
    'attempt_select': False,  # v1.7: bridge-aware attempt selection in finish_leg -- DEFAULT OFF: measured 2026-11-22 head-to-head vs v1.6.0 on srambo (6 nets) + Manikin (4 nets): outcomes identical on all 10, 0 closures, probes cost +14-39s where they engage; enable for the attempts_probed diagnostics
    'ilp_repair_cap': 0,      # AUTO: joint ILP repair per run (v1.5, phase (c) Stage B) -- DEFAULT OFF: measured 2026-11-22, the ILP closes nothing yet (execution/re-bridge binds, not selection); enable per-run to experiment     # AUTO: max large-window hard-A* long_finish attempts per run (v1.4, phase (c) Stage B-0)     # AUTO: max pour-net finish_leg attempts per run (was hard-coded 2)
    'work': 20000000,
    'no_tracks_on': 'In1.Cu',
    'layer_law_policy': 'auto',  # v1.8: B-first + escalate on >=4L In2-pour boards (dataset-v3 gap)
    'strip_existing': True,
    # v1.11.5 (#150/#138): the ONLY engineered-nets keys admitted
    # to DEFAULTS -- plain booleans. power_netlist / kb_cache /
    # kb_endpoint deliberately stay OUT: they are paths/URLs and
    # remain server-owned (set in server config, never forwarded
    # from job overrides).
    'engineered_nets': True,
    'placement_audit': True,
    'max_tool_calls': 48,
    'request_timeout_s': 600,
    'use_backend': False,
    'backend_url': 'http://127.0.0.1:8731',
    'backend_token': '',
}

def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg

def save(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)
