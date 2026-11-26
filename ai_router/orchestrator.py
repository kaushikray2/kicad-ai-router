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

"""Orchestrator: drives the deterministic pipeline tools, either with an
LLM (OpenAI-compatible endpoint) making the strategy decisions, or with a
deterministic AUTO policy that encodes the same playbook as rules.

The LLM never emits geometry. It only chooses among verified tool moves;
constraints (layer law, clearances) are enforced inside the tools."""
import json
try:
    from .llm_client import LLMClient, LLMError
except ImportError:
    from llm_client import LLMClient, LLMError
try:
    from .pipeline import Pipeline, cost_tail, fault_count, parse_layer_cost, starved_delta
except ImportError:
    from pipeline import Pipeline, cost_tail, fault_count, parse_layer_cost, starved_delta
try:
    from .config import DEFAULTS as _CFG_DEFAULTS
except Exception:
    try:
        from config import DEFAULTS as _CFG_DEFAULTS
    except Exception:
        _CFG_DEFAULTS = {}

PLAYBOOK = """You are the strategy layer of a PCB routing pipeline. Read the
board's actual stackup from board_summary (copper_layers, pour_nets): on the
4-layer test board that is F.Cu / B.Cu signal, In1.Cu GND plane, and an In2.Cu
+3V3 plane that IS routable unless the run's layer law bans it (no_tracks_on).
A deterministic
router (TraceMaker) and scripted finishers do ALL geometry. You never draw
tracks; you decide priorities and which tool runs next, using tool results.

Procedure (measured on real boards; follow it):
1. Call board_summary and scorecard first to orient.
2. Call route with first_nets = the RF net plus the main power nets
   (e.g. GNSS_RF_IN, +5V, V_BAT+). first_nets route first and other nets
   may not rip them.
3. Call gate after every route. Judge by: signal_items / split_signal_nets =
   real failures; pour_items = GND pour fragmentation (NOT signal
   failures; they are handled at the end by stitch_gnd).
4. Triage: if a pass leaves failed signal connections, promote the failed
   nets into first_nets (keep the earlier ones) and route again from the
   clean base. Do at most 4 route passes.
5. If the SAME net fails on two passes, stop re-running the whole board:
   call finish_leg for that net (it displaces one blocking net locally,
   routes the leg, and re-bridges the displaced net).
5b. If finish_leg cannot close a net whose fragments sit far apart,
   call long_finish for that net: a large-window hard A* over several
   anchor pairs. It displaces nothing -- it draws only when a fully
   legal path exists, so a no_path there means genuine congestion.
5c. If several signal nets in the same area stay split after that,
   call ilp_repair with the group: it prices every net's displacement
   candidates and picks a joint, conflict-free set (0-1 ILP). A group
   that comes back unrouted is congestion-bound; report it as such.
6. When signal failures reach zero, call stitch_gnd once, then gate.
7. Call done when the gate shows 0 unconnected, or when no tool can make
   further progress. Report the final numbers honestly.

Rules: never put GND or 3V3 in first_nets -- they are plane nets, served
by the pours, and are never track-routed. Prefer route from the clean
base (from_current=false) for triage passes. When you call done, quote
the LAST gate result's exact numbers; never claim a net is resolved
unless the gate after the fix shows it.
Never ask to route on In1.Cu (the GND plane); the tools enforce
this anyway. Do not repeat a tool call with identical arguments. If a
tool errors, read the error and adapt; do not retry it verbatim."""

TOOLS = [
    {'type': 'function', 'function': {'name': 'board_summary', 'description': 'Board orientation: nets, pad counts, split nets (pads in >1 component), track/via counts.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'scorecard', 'description': 'Physics scorecard: worst decoupling caps by distance to nearest same-net pad.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'route', 'description': 'One TraceMaker pass. From the clean base unless from_current is true.', 'parameters': {'type': 'object', 'properties': {'first_nets': {'type': 'array', 'items': {'type': 'string'}}, 'work': {'type': 'integer'}, 'from_current': {'type': 'boolean'}}}}},
    {'type': 'function', 'function': {'name': 'gate', 'description': 'KiCad DRC gate with zone refill: unconnected decomposed into pour vs signal, violation classes, split nets.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'finish_leg', 'description': 'Scripted in-place finisher for one net: grid A*, single-net displacement if blocked, re-bridge of the displaced net.', 'parameters': {'type': 'object', 'properties': {'net': {'type': 'string'}}, 'required': ['net']}}},
    {'type': 'function', 'function': {'name': 'long_finish', 'description': 'Large-window hard-A* finisher for one split signal net: tries several cross-cluster anchor pairs over a board-scale window, no displacement. For far-apart fragments finish_leg cannot reach.', 'parameters': {'type': 'object', 'properties': {'net': {'type': 'string'}}, 'required': ['net']}}},
    {'type': 'function', 'function': {'name': 'ilp_repair', 'description': 'Joint 0-1 ILP repair for a group of split signal nets in the same area: prices each net\'s displacement candidates, builds the exact conflict graph, and executes a jointly compatible set of legs.', 'parameters': {'type': 'object', 'properties': {'nets': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['nets']}}},
    {'type': 'function', 'function': {'name': 'stitch_gnd', 'description': 'Find floating GND pad clusters (pour islands) and stitch each to the plane with a via.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'heal', 'description': 'Rejoin split pour-net (plane) fragments with additive bridges, including a via hunt through the fragment plane island when its pads sit in a copper pocket. Gate-verified.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'prune', 'description': 'Remove dead copper islands (track/via components touching no pad and no zone). Self-verified; cannot change pad connectivity.', 'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'done', 'description': 'End the run with a summary note.', 'parameters': {'type': 'object', 'properties': {'note': {'type': 'string'}}}}},
]

def _dispatch(pipe, name, args):
    if name == 'board_summary': return pipe.tool_board_summary()
    if name == 'scorecard': return pipe.tool_scorecard()
    if name == 'route': return pipe.tool_route(args.get('first_nets') or [], args.get('work'), bool(args.get('from_current')))
    if name == 'gate': return pipe.tool_gate()
    if name == 'finish_leg': return pipe.tool_finish_leg(args.get('net', ''))
    if name == 'long_finish': return pipe.tool_long_finish(args.get('net', ''))
    if name == 'ilp_repair': return pipe.tool_ilp_repair(args.get('nets') or [])
    if name == 'stitch_gnd': return pipe.tool_stitch()
    if name == 'heal': return pipe.tool_heal()
    if name == 'prune': return pipe.tool_prune()
    raise ValueError('unknown tool ' + name)

def _trim(result, limit=3500):
    s = json.dumps(result)
    return s if len(s) <= limit else s[:limit] + '...[trimmed]'

def _cfg_int(cfg, key, default):
    """Config int with a forgiving read (#138 (B13) smaller
    item): a non-numeric max_tool_calls used to crash an
    LLM-mode job outright at int(...); fall back to the default
    instead.  (The backend's S6 clamps in run_job cover job
    overrides; this covers every other config source.)"""
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _route_info(pipe):
    # v1.12.2 (#170): the last engine pass's structured contract,
    # carried into the run result (the backend writes it into the
    # job status; the dialog result keeps it too).
    lr = getattr(pipe, 'last_route', None) or {}
    return {k: lr.get(k) for k in ('exit_code', 'stopped_by', 'route_open_only', 'open_at_start')}


def run_llm(cfg, pipe, log):
    client = LLMClient(cfg['endpoint'], cfg['model'], cfg.get('api_key', ''), cfg.get('request_timeout_s', 600))
    messages = [{'role': 'system', 'content': PLAYBOOK},
                {'role': 'user', 'content': 'Route this board. Board file: %s. Begin.' % pipe.board_path}]
    max_calls = _cfg_int(cfg, 'max_tool_calls', 48)
    calls_made = 0
    while calls_made < max_calls:
        try:
            resp = client.chat(messages, TOOLS)
        except LLMError as e:
            log('LLM error: %s — falling back to AUTO policy for the remainder' % e)
            return run_auto(cfg, pipe, log)
        msg = {'role': 'assistant', 'content': resp['content'] or ''}
        if resp['raw'].get('tool_calls'):
            msg['tool_calls'] = resp['raw']['tool_calls']
        messages.append(msg)
        if resp['content']:
            log('LLM: ' + resp['content'][:300])
        if not resp['tool_calls']:
            log('LLM ended the run without a done call.')
            break
        finished = False
        for call in resp['tool_calls']:
            calls_made += 1
            name, args = call['name'], call['args']
            log('Tool: %s %s' % (name, json.dumps(args)[:160]))
            if name == 'done':
                log('LLM done: ' + str(args.get('note', ''))[:300])
                finished = True
                break
            try:
                result = _dispatch(pipe, name, args)
                payload = _trim(result)
            except Exception as e:
                payload = json.dumps({'error': str(e)[:400]})
            messages.append({'role': 'tool', 'tool_call_id': call['id'] or 'x', 'content': payload})
        if finished: break
    final = pipe.finalize()
    gate = pipe.tool_gate(final)  # v1.8.2 (#18): report the gate of the DELIVERED file
    return {'final_board': final, 'gate': gate, 'route': _route_info(pipe)}

def default_first_nets(pipe):
    summ = pipe.tool_board_summary()
    # Nets with fewer than 2 pads can never be routed; the summary's
    # 1-pad pseudo-nets (e.g. 'unconnected-(U6-VDD_RF-Pad14)') otherwise
    # leak into first_nets via the RF substring match and waste a slot.
    names = [n['net'] for n in summ['nets'] if n.get('pads', 0) >= 2]
    picks = []
    for n in names:
        if 'RF' in n and 'GND' not in n: picks.append(n)
    for n in names:
        if n.startswith('+') or n.startswith('V_') or n.endswith('+'): picks.append(n)
    seen, out = set(), []
    for p in picks:
        if p not in seen and p not in ('GND', '3V3'):
            seen.add(p); out.append(p)
    return out[:6]

def _unc_key(g):
    # v1.8.2 (#13): None-safe -- 'unconnected_total or 9999' mapped a
    # fully connected board (0) to 9999, the WORST key, so the pass
    # loop restored worse passes over perfect ones and a layer-law
    # escalation that connected everything lost to the split variant.
    u = (g or {}).get('unconnected_total')
    return 10 ** 9 if u is None else u

def _final_key(g):
    # v1.11.4 (#115): the same layer-cost tail as _accept_key,
    # appended last -- this key's (unconnected, splits, faults)
    # order is deliberate (#37) and unchanged; the tail only breaks
    # full ties, so between two passes equal on the gate the cheaper
    # one now wins instead of whichever came first.
    # v1.11.5 (#116): the starved-thermal delta tier joins the final
    # key too, after faults and before the cost tail (same subordination
    # as in _accept_key).
    return (_unc_key(g), len(g.get('split_nets') or []), fault_count(g), starved_delta(g), cost_tail(g))

def _b_needs_escalation(gate):
    # v1.12.2 (#179): B is kept only when it left NOTHING split.
    # The original condition looked at split SIGNAL nets alone, so
    # a B run whose only splits were POUR nets (the v1.12.2 Stage C
    # sample-2 shape: split_pour [GND], split_signal []) never
    # escalated -- yet a pour split is exactly what Variant A's
    # different layer law can change. A is still kept only when
    # the final key prefers it, so this can only add the A run,
    # never force its result.
    g = gate or {}
    return bool((g.get('split_signal_nets') or []) or (g.get('split_pour_nets') or []))

def run_auto(cfg, pipe, log):
    # v1.8 layer-law policy (dataset-v3: SBS 333 vs VBS 289 on 54
    # multilayer boards -- the 44-connection gap is layer-law-driven).
    # On a >=4-layer board with an In2.Cu pour, run Variant B (In2
    # banned) first: B keeps the plane solid and wins or ties on most
    # boards. Escalate to Variant A only when B leaves split SIGNAL
    # nets, and keep the better final by the run key. Simulated on the
    # grid: policy total 309 vs A-fixed 334 / B-fixed 340 (oracle 290),
    # escalating on 21 of 54 boards. An explicit non-default law or
    # layer_law_policy='off' disables it.
    _law_raw = [x.strip() for x in str(cfg.get('no_tracks_on') or 'In1.Cu').split(',') if x.strip()]  # v1.9 (M4): set compare -- the old string equality misread 'In1.Cu ' / spacing variants as a non-default law and skipped the policy
    # v1.11 (#73): an explicit layer_cost disables the policy, the
    # same precedent as an explicit non-default no_tracks_on -- the
    # user has priced the layers themselves, so the run must not
    # silently re-ban one of them as a 'variant'. An invalid spec is
    # treated as off by the pipeline, so it does not disable
    # anything here either.
    _cost_pairs, _cost_err = parse_layer_cost(cfg.get('layer_cost', ''))
    if _cost_pairs:
        log('Layer-law policy: explicit layer_cost (%s) -- auto escalation disabled' % cfg.get('layer_cost'))
        return _run_auto_once(cfg, pipe, log)
    _law = set(_law_raw); _summ0 = None
    if cfg.get('layer_law_policy', 'auto') == 'auto':
        # v1.10.1 (#49): resolve the law against the board's own layer
        # names before comparing -- 'GND' or 'in1.cu' in the config IS
        # the default law on a board that names In1.Cu 'GND'.
        _summ0 = pipe.tool_board_summary()
        _aliases = _summ0.get('layer_aliases') or {}
        _law = {_aliases.get(x.lower(), x) for x in _law_raw}
    if cfg.get('layer_law_policy', 'auto') == 'auto' and _law == {'In1.Cu'}:
        summ = _summ0
        layers = summ.get('copper_layers') or []
        pour_layers = summ.get('pour_layers') or {}
        if len(layers) >= 4 and any('In2.Cu' in (ls or []) for ls in pour_layers.values()):
            orig = cfg.get('no_tracks_on')
            cfg['no_tracks_on'] = 'In1.Cu,In2.Cu'
            log('Layer-law policy: Variant B (In2 banned) first on this %d-layer pour board' % len(layers))
            res_b = _run_auto_once(cfg, pipe, log)
            if orig is None: cfg.pop('no_tracks_on', None)
            else: cfg['no_tracks_on'] = orig
            if not _b_needs_escalation(res_b['gate']):
                log('Layer-law policy: B left no split nets -- keeping B')
                return res_b
            log('Layer-law policy: B left split nets (signal %s, pour %s) -- escalating to Variant A' % (res_b['gate'].get('split_signal_nets'), res_b['gate'].get('split_pour_nets')))
            import time as _time; _time.sleep(1.1)  # pipe2 needs a distinct workdir stamp
            cfg2 = dict(cfg); cfg2['no_tracks_on'] = 'In1.Cu'
            res_a = _run_auto_once(cfg2, Pipeline(cfg2, pipe.board_path, log), log)
            ka, kb = _final_key(res_a['gate']), _final_key(res_b['gate'])
            log('Layer-law policy: A key %s vs B key %s -- keeping %s' % (ka, kb, 'A' if ka < kb else 'B'))
            return res_a if ka < kb else res_b
    return _run_auto_once(cfg, pipe, log)

def _run_auto_once(cfg, pipe, log):
    log('AUTO policy: base gate + summary + scorecard')
    pipe.tool_gate()  # base gate: input-board DRC, so later gates read as deltas
    pipe.tool_board_summary()
    pipe.tool_scorecard()
    policy = cfg.get('first_nets_policy', 'auto')
    if policy == 'none':
        first = []
    elif policy == 'most_pads':
        summ = pipe.tool_board_summary()
        pour = set(summ.get('pour_nets') or [])
        cand = [n for n in summ['nets'] if n.get('pads', 0) >= 2 and n['net'] not in pour]
        cand.sort(key=lambda n: -n.get('pads', 0))
        first = [n['net'] for n in cand[:12]]
    else:
        first = default_first_nets(pipe)
    log('Initial first_nets: %s' % first)
    max_passes = int(cfg.get('max_passes', 4))
    scheme = str(cfg.get('pass_advice', 'promote')).lower()
    prio = None; regs = None
    failed_once, failed_twice = set(), set()
    gate = None
    best = None
    prev_sig = None
    base_gate = pipe.tool_gate(pipe.base)  # v1.9.6 (#37): the portfolio skip guard needs the base fault count
    # v1.11.5 (#123): the literal fallback matches config.py ('tm_relax', was '')
    engines = [e.strip() for e in str(cfg.get('engine_portfolio', _CFG_DEFAULTS.get('engine_portfolio', 'tm_relax'))).split(',') if e.strip()]
    if engines:
        try:
            pg = pipe.tool_portfolio(engines)
        except Exception as e:
            log('Portfolio stage failed (%s) -- continuing with TraceMaker passes' % e)
            pg = None
        if pg is not None:
            if (pipe.last_gate or {}).get('board') != pipe.current:
                pg = pipe.tool_gate(pipe.current)  # last_gate may hold a losing candidate's gate
            best = (_final_key(pg), pipe.current)  # v1.11.4 (#115): was an inline copy of this key's shape
            gate = pg
            # v1.9.6 (#37): skip passes ONLY for a winner that is fully
            # connected (any net kind -- split_nets excludes GND, so
            # pour splits are checked explicitly) AND no dirtier than
            # the base board. A dirty-but-connected --relax winner
            # previously locked its copper faults in unseen.
            splits_any = (pg.get('split_nets') or []) + (pg.get('split_pour_nets') or [])
            if _unc_key(pg) == 0 and not splits_any and fault_count(pg) <= fault_count(base_gate):
                log('Portfolio board is fully connected and no dirtier than base -- skipping TraceMaker passes')
                max_passes = 0
    for pno in range(1, max_passes + 1):
        try:
            pipe.tool_route(first_nets=first, priority=prio, regions=regs)
        except Exception as e:
            log('Route pass %d failed (%s) — finishing with the best board so far' % (pno, e))
            break
        gate = pipe.tool_gate()
        failing = list(gate.get('split_nets') or [])
        key = _final_key(gate)  # v1.11.4 (#115): len(failing) IS len(gate's split_nets); one key definition, no inline copies
        if best is None or key < best[0]: best = (key, pipe.current)
        if not failing:
            log('All signal nets connected after pass %d' % pno)
            break
        pour = set(gate.get('pour_nets') or [])
        signal_failing = [n for n in failing if n not in pour]
        if not signal_failing:
            log('Only pour nets remain split (%s) — track passes cannot fix pour islands; going to stitch' % failing)
            break
        sig = (gate.get('unconnected_total'), tuple(sorted(signal_failing)))
        if prev_sig is not None and sig == prev_sig:
            log('Pass %d gate identical to the previous pass — plateau; going to finishing' % pno)
            break
        prev_sig = sig
        for n in signal_failing:
            if n in failed_once: failed_twice.add(n)
            failed_once.add(n)
        if scheme == 'priority':
            # v1.9 (Phase 1.3): first_nets stays the initial critical
            # set; the failing CONNECTIONS are ordered via priority
            # pairs from the pass's stuck report (routed first, but
            # NOT protected from rip-up), hardest first.
            stuck_u = (getattr(pipe, 'last_stuck', None) or {}).get('unrouted') or []
            cand = [u for u in stuck_u if u.get('net') not in pour]
            cand.sort(key=lambda u: (-(u.get('fails') or 0), u.get('length_mm') or 0))
            prio = [[u['from'], u['to']] for u in cand[:512]]
            regs = None
            if cfg.get('advice_regions'):
                regs = [{'rect': u['region_mm'], 'reserve_for': [u['net']]} for u in cand[:24] if u.get('region_mm')]
            log('Pass %d failing: %s (priority advice: %d pairs%s)' % (pno, signal_failing, len(prio), (', %d regions' % len(regs)) if regs else ''))
        else:
            for n in signal_failing:
                if n not in first: first.append(n)
            log('Pass %d failing: %s (promoted into first_nets)' % (pno, signal_failing))
        if pno == max_passes: break
    if best and pipe.current != best[1]:
        log('Restoring best pass board (%d unconnected) over the last pass' % best[0][0])
        pipe.current = best[1]
    # finish legs for nets that failed twice (or are still failing)
    gate = pipe.tool_gate()
    pour = set(gate.get('pour_nets') or [])
    still = [n for n in (gate.get('split_nets') or []) if n not in pour]
    for n in still[:int(cfg.get('finish_signal_cap', 8))]:
        try: pipe.tool_finish_leg(n)
        except Exception as e: log('finish_leg %s failed: %s' % (n, e))
    # v1.4 phase (c) Stage B-0: signal nets still split after local
    # finishing get the large-window hard-A* attempt (far-apart
    # components class). Signal only -- pour fragments belong to
    # stitch/heal below.
    gate = pipe.tool_gate()
    long_still = list(gate.get('split_signal_nets') or [])
    for n in long_still[:int(cfg.get('long_finish_cap', 6))]:
        try: pipe.tool_long_finish(n)
        except Exception as e: log('long_finish %s failed: %s' % (n, e))
    # v1.5 phase (c) Stage B: signal nets STILL split are handed to
    # the joint ILP repair in groups of up to 6 -- the selection is
    # joint exactly where sequential greedy legs interact (shared
    # victims, crossing corridors).
    gate = pipe.tool_gate()
    ilp_still = list(gate.get('split_signal_nets') or [])[:int(cfg.get('ilp_repair_cap', 0))]  # v1.9 (L2): fallback was 6 here vs 0 in config/pipeline -- ILP could run when the config never enabled it
    for k in range(0, len(ilp_still), 6):
        try: pipe.tool_ilp_repair(ilp_still[k:k + 6])
        except Exception as e: log('ilp_repair %s failed: %s' % (ilp_still[k:k + 6], e))
    # Pour-net splits left by the passes (a fragmented power plane when
    # the plane layer is routable) get finish_leg attempts too: stitch
    # only heals floating pads, it cannot rejoin plane fragments.
    gate = pipe.tool_gate()
    pour = set(gate.get('pour_nets') or [])
    pour_still = list(gate.get('split_pour_nets') or [n for n in (gate.get('split_nets') or []) if n in pour])
    for n in pour_still[:int(cfg.get('finish_pour_cap', 4))]:
        try: pipe.tool_finish_leg(n)
        except Exception as e: log('finish_leg (pour) %s failed: %s' % (n, e))
    gate = pipe.tool_gate()
    # Stitch whenever pour copper is still split or short, even if signal
    # nets also remain: v1 skipped it unless EVERY split net was a pour
    # net, which left fixable pour islands on the table in backend runs.
    if (gate.get('pour_items') or 0) > 0 or (gate.get('split_pour_nets') or []):
        try: pipe.tool_stitch()
        except Exception as e: log('stitch failed: %s' % e)
        gate = pipe.tool_gate()
    # Heal pour fragments that carry vias/stubs: stitch only sees
    # floating clusters and finish_leg plans pad-to-pad, so a plane
    # fragment with its own vias is invisible to both (the 3V3 case).
    if (gate.get('split_pour_nets') or []):
        try: pipe.tool_heal()
        except Exception as e: log('heal failed: %s' % e)
        gate = pipe.tool_gate()
    try: pipe.tool_prune()
    except Exception as e: log('prune failed: %s' % e)
    gate = pipe.tool_gate()
    # v1.8 (#2): one more stitch+heal pass AFTER prune. Measured on the
    # 22 dataset-v3 pour-only finals: a fresh pour pass on the finished
    # board closes boards the mid-sequence pass cannot -- prune removes
    # dead copper that blocked bridges, and the finish stages change
    # the board after the first heal ran. Across the 22 boards the
    # upgraded heal alone took unconnected items 83 -> 58 (three full
    # closures even with the stock heal). Both tools are internally
    # gate-verified, like every other stage here.
    if (gate.get('pour_items') or 0) > 0 or (gate.get('split_pour_nets') or []):
        log('post-prune pour pass (key before: %s)' % (_final_key(gate),))
        try: pipe.tool_stitch()
        except Exception as e: log('post-prune stitch failed: %s' % e)
        try: pipe.tool_heal()
        except Exception as e: log('post-prune heal failed: %s' % e)
        gate = pipe.tool_gate()
        log('post-prune pour pass done (key after: %s)' % (_final_key(gate),))
    # v1.9 (Phase 1.5): windowed engine re-route on the surviving split
    # signal nets -- the engine re-routes inside a locked-copper window
    # around each stuck connection. Off by default (cap 0); every
    # attempt is gate-verified inside the tool.
    rwcap = int(cfg.get('reroute_window_cap', 0) or 0)
    if rwcap > 0:
        sig = list(gate.get('split_signal_nets') or [])[:rwcap]
        if sig:
            log('reroute_window stage: %s' % (sig,))
            for net in sig:
                try: pipe.tool_reroute_window(net, margin=float(cfg.get('reroute_margin', 6.0)))
                except Exception as e: log('reroute_window %s failed: %s' % (net, e))
            gate = pipe.tool_gate()
    final = pipe.finalize()
    gate = pipe.tool_gate(final)  # v1.8.2 (#18): the reported gate measures the delivered file
    log('AUTO done: %s' % json.dumps({k: gate.get(k) for k in ('unconnected_total', 'pour_items', 'split_nets', 'violations_total')}))
    return {'final_board': final, 'gate': gate, 'route': _route_info(pipe)}
