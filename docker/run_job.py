#!/usr/bin/env python3
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

"""One routing job, run as a subprocess by server.py. Drives the existing
ai_router Pipeline + orchestrator on the job's input board; writes the
terminal status to <job-dir>/status.json and the result board beside it."""
import argparse, json, os, shutil, subprocess, sys, time

def find_plugin():
    cands = [os.path.dirname(os.path.abspath(__file__)),
             os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    if os.environ.get('AIR_PLUGIN_PATH'): cands.insert(0, os.environ['AIR_PLUGIN_PATH'])
    for c in cands:
        if os.path.isfile(os.path.join(c, 'ai_router', 'pipeline.py')):
            return c
    raise RuntimeError('ai_router package not found in %s' % cands)

_ENGINES = ('freerouting', 'krt', 'tm_relax', 'tm_global', 'tm_escape')

def _int_or(rt, k, d):
    try: return int(rt.get(k, d))
    except Exception: return d

def _engines_or(rt, d):
    # v1.10.1 (#56): whitelist + dedupe + cap -- a job could previously
    # list tm_relax fifty times at up to 30 min per candidate.
    raw = rt.get('engine_portfolio', d)
    items = [str(x) for x in raw] if isinstance(raw, (list, tuple)) else [x.strip() for x in str(raw).split(',')]
    out = []
    for e in items:
        if e in _ENGINES and e not in out: out.append(e)
    return ','.join(out[:4])

def _deadline_epoch_for(cfg, now):
    # #61: the configured limit (a duration) becomes ONE absolute
    # deadline -- a single point in time every worker in the run
    # compares against, never a per-worker duration.
    try: limit = int(cfg.get('time_limit_s') or 0)
    except Exception: limit = 0
    return (now + limit) if limit > 0 else None

def _atomic_copy(src, dst):
    # #61: write beside the target, rename over it -- the
    # write_status pattern, for file copies: the artifact is
    # always a complete file.
    tmp = dst + '.tmp-%d' % os.getpid()
    shutil.copy(src, tmp)
    os.replace(tmp, dst)

def build_cfg(rt, llm, tm_exe):
    # v1.10.1 (#43): ONE source of defaults. The old constructor
    # hardcoded a subset, and the forwarding loop below copied a
    # plugin DEFAULTS key only if the merged config already contained
    # it -- so backend jobs silently ran without engine_portfolio
    # (the shipped default since v1.9.5) and with reroute_accept's
    # dead 'faults' fallback. Order: plugin DEFAULTS, then the
    # server-owned keys, then the job's routing values, parsed
    # defensively (a non-numeric max_passes used to crash the job
    # before the S6 clamps could drop it).
    cfg = {}
    try:
        for _p in (find_plugin(), os.path.join(find_plugin(), 'ai_router')):
            if _p not in sys.path: sys.path.insert(0, _p)
        import config as _cm
        cfg.update(_cm.DEFAULTS)
    except Exception as e:
        # v1.11.5 (#123): never silent -- a failed config import was
        # the N1 failure mode (jobs running on hard-coded fallbacks
        # with no trace). The job continues on the server-owned keys
        # below, but the failure is on the record.
        print('build_cfg: plugin config import failed (%r); continuing without DEFAULTS' % (e,), file=sys.stderr, flush=True)
    cfg.update({'endpoint': llm.get('endpoint', ''), 'model': llm.get('model', ''),
                'api_key': llm.get('api_key', ''), 'tracemaker_path': tm_exe, 'kicad_cli': 'kicad-cli'})
    cfg['mode'] = rt.get('mode', cfg.get('mode', 'auto'))
    cfg['max_passes'] = _int_or(rt, 'max_passes', cfg.get('max_passes', 4))
    cfg['work'] = _int_or(rt, 'work', cfg.get('work', 40000000))
    cfg['no_tracks_on'] = rt.get('no_tracks_on', cfg.get('no_tracks_on', 'In1.Cu'))
    cfg['strip_existing'] = bool(rt.get('strip_existing', cfg.get('strip_existing', True)))
    for _k, _d in (('finish_signal_cap', 8), ('finish_pour_cap', 4), ('long_finish_cap', 6), ('ilp_repair_cap', 0)):
        cfg[_k] = _int_or(rt, _k, cfg.get(_k, _d))
    cfg['attempt_select'] = bool(rt.get('attempt_select', cfg.get('attempt_select', False)))
    cfg['deterministic'] = bool(rt.get('deterministic', cfg.get('deterministic', False)))  # v1.12.2 (#171)
    cfg['time_limit_s'] = _int_or(rt, 'time_limit_s', cfg.get('time_limit_s', 0))  # Phase 2E (#61)
    cfg['route_open_only'] = bool(rt.get('route_open_only', cfg.get('route_open_only', False)))  # v1.12.2 (#171)
    if cfg['mode'] == 'llm' and cfg['deterministic']:
        # v1.12.2 (#185): Reproducible is an Auto-mode property --
        # the LLM path varies run to run by design. The dialog
        # clears the checkbox under LLM and the config page guards
        # it, but a saved llm+deterministic pair must never reach
        # a run from ANY client, so the coercion lives here too.
        cfg['deterministic'] = False
        print('build_cfg: deterministic forced off -- mode is llm (Reproducible is an Auto-mode property)', file=sys.stderr, flush=True)
    cfg['layer_law_policy'] = rt.get('layer_law_policy', cfg.get('layer_law_policy', 'auto'))
    cfg['layer_cost'] = rt.get('layer_cost', cfg.get('layer_cost', ''))  # v1.11 (#73): explicit map, like layer_law_policy; the generic forwarding loop below would also carry it from DEFAULTS
    cfg['first_nets_policy'] = rt.get('first_nets_policy', cfg.get('first_nets_policy', 'auto'))
    cfg['engine_portfolio'] = _engines_or(rt, cfg.get('engine_portfolio', ''))
    # v1.11.1 (#74): forward every remaining DEFAULTS key the job set.
    # The explicit mappings above are the parsed few; main()'s generic
    # forwarding loop can never fire because cfg already holds every
    # DEFAULTS key (its 'not in cfg' test is always False), so before
    # this, overrides for reroute_window_cap / reroute_accept /
    # reroute_margin / pass_advice / advice_regions / max_tool_calls
    # were silently dropped and the job ran with the shipped defaults.
    _handled = {'endpoint', 'model', 'api_key', 'tracemaker_path', 'kicad_cli',
                'mode', 'max_passes', 'work', 'no_tracks_on', 'strip_existing',
                'finish_signal_cap', 'finish_pour_cap', 'long_finish_cap',
                'ilp_repair_cap', 'attempt_select', 'layer_law_policy',
                'layer_cost', 'first_nets_policy', 'engine_portfolio',
                'deterministic', 'route_open_only', 'time_limit_s'}
    try:
        for _k in _cm.DEFAULTS:
            if _k not in _handled and _k in rt: cfg[_k] = rt[_k]
    except Exception as e:
        print('build_cfg: DEFAULTS forwarding failed (%r)' % (e,), file=sys.stderr, flush=True)  # v1.11.5 (#123)
    return cfg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--job-dir', required=True)
    a = ap.parse_args()
    jd = a.job_dir
    status_p = os.path.join(jd, 'status.json')
    rec = json.load(open(status_p)) if os.path.exists(status_p) else {}
    def write_status(**kw):
        rec.update(kw)
        tmp = status_p + '.tmp'
        with open(tmp, 'w') as f: json.dump(rec, f, indent=1)
        os.replace(tmp, status_p)
    try:
        cfgj = json.load(open(os.path.join(a.data, 'config', 'config.json')))
        llm = cfgj.get('llm', {}); rt = dict(cfgj.get('routing', {}))
        rt.update(rec.get('overrides') or {})
        tm_ver = (cfgj.get('tracemaker') or {}).get('selected_version', '')
        if not tm_ver:
            tm_dir = os.path.join(a.data, 'tracemaker')
            installed = [d for d in sorted(os.listdir(tm_dir))
                         if os.path.isfile(os.path.join(tm_dir, d, 'bin', 'tracemaker'))] if os.path.isdir(tm_dir) else []
            if len(installed) == 1: tm_ver = installed[0]
        tm_exe = os.path.join(a.data, 'tracemaker', tm_ver, 'bin', 'tracemaker')
        if not os.path.isfile(tm_exe):
            raise RuntimeError('selected TraceMaker %r not installed' % tm_ver)
        kc = subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
        # pinning: the resolved absolute path + versions are fixed for this run
        write_status(tracemaker_version=tm_ver, tracemaker_path=tm_exe, kicad_cli=kc)
        cfg = build_cfg(rt, llm, tm_exe)
        sys.path.insert(0, find_plugin())
        sys.path.insert(0, os.path.join(find_plugin(), 'ai_router'))
        # v1.11.2 (#108): forwarding lives in build_cfg alone since
        # #74 -- it starts from the full plugin DEFAULTS and copies
        # every remaining job-set key. The v1.8.2 generic loop that
        # used to sit here tested '_k not in cfg', which build_cfg
        # made permanently False; removed as dead code (its comment
        # also misdescribed the flow and invited fixes in the wrong
        # place). The effective merged config is recorded in the job
        # status below for audit.
        # v1.9 (S6): job overrides are untrusted input to a shared
        # backend -- clamp the numeric routing params to sane ranges
        # (a max_passes of 100000 or a negative work budget otherwise
        # runs effectively forever). Unparseable values are dropped,
        # leaving the pipeline's own fallbacks.
        _CLAMPS = {'max_passes': (1, 8), 'work': (100000, 500000000),
                   'finish_signal_cap': (0, 32), 'finish_pour_cap': (0, 32),
                   'long_finish_cap': (0, 32), 'ilp_repair_cap': (0, 8),
                   'reroute_window_cap': (0, 16), 'reroute_margin': (0.5, 50.0),
                   'time_limit_s': (0, 86400)}
        for _k, (_lo, _hi) in _CLAMPS.items():
            if _k in cfg:
                try: cfg[_k] = max(_lo, min(_hi, type(_lo)(cfg[_k])))
                except Exception: cfg.pop(_k, None)
        # #61: the limit becomes ONE absolute deadline, computed
        # once here and shared by every worker in the run -- the
        # pipeline reads cfg['deadline_epoch']; subprocess workers
        # inherit AIR_DEADLINE_EPOCH. It is set before
        # cfg_effective is written, so the audited record carries
        # it too.
        _deadline = _deadline_epoch_for(cfg, time.time())
        if _deadline is not None:
            cfg['deadline_epoch'] = _deadline
            os.environ['AIR_DEADLINE_EPOCH'] = repr(_deadline)
        write_status(cfg_effective={k: v for k, v in cfg.items() if k != 'api_key'},
                     **({'time_limit_s': cfg.get('time_limit_s'), 'deadline_epoch': _deadline} if _deadline is not None else {}))
        from pipeline import Pipeline
        import orchestrator
        def log(m): print('[job] ' + str(m), flush=True)
        pipe = Pipeline(cfg, os.path.join(jd, 'input.kicad_pcb'), log)
        # #61: best-so-far checkpoints land on the job's result
        # artifact from the first gated pass on -- a kill, cancel
        # or server timeout at any later point leaves the best
        # routing found so far as a complete, gate-checkable
        # board instead of nothing.
        pipe.best_so_far_path = os.path.join(jd, 'result.kicad_pcb')
        if _deadline is not None:
            log('Deadline: %ss limit -- absolute deadline epoch %.3f shared by every worker (#61)' % (cfg.get('time_limit_s'), _deadline))
        res = orchestrator.run_auto(cfg, pipe, log) if cfg['mode'] == 'auto' else orchestrator.run_llm(cfg, pipe, log)
        final = res.get('final_board')
        if final and os.path.exists(final):
            # #61: every artifact write is atomic -- the
            # checkpoint hook has been landing best-so-far boards
            # on this path all run; the final write replaces the
            # last checkpoint the same way.
            _atomic_copy(final, os.path.join(jd, 'result.kicad_pcb'))
            # v1.8.2 (#17): the result's project sidecars travel with it
            for _ext in ('.kicad_pro', '.kicad_dru'):
                _sc = os.path.splitext(final)[0] + _ext
                if os.path.isfile(_sc): _atomic_copy(_sc, os.path.join(jd, 'result' + _ext))
            # v1.8.1 (#8): serve the WINNING pipeline's ledger -- after a
            # layer-law escalation two workdirs exist and the later one
            # is not necessarily the variant that was kept.
            _led = os.path.join(os.path.dirname(final), 'ledger.json')
            if os.path.isfile(_led): _atomic_copy(_led, os.path.join(jd, 'ledger.json'))
        gate = res.get('gate') or {}
        _dl = {}
        if _deadline is not None:
            # #61: a result the deadline truncated is labelled
            # non-deterministic (machine-speed dependent); a
            # deadline that never bound changed nothing about
            # the run and labels nothing.
            _dl = {'deadline_epoch': _deadline, 'deadline_hit': bool(res.get('deadline_hit')),
                   'nondeterministic': bool(res.get('deadline_hit'))}
        write_status(status='done', gate={k: gate.get(k) for k in ('unconnected_total', 'pour_items', 'split_nets', 'split_signal_nets', 'split_pour_nets', 'pour_nets', 'violations_total', 'copper_violations', 'vias', 'wirelength_mm', 'banned_layer_tracks', 'banned_layer_tracks_delta', 'tracks_by_layer')}, route=res.get('route'), **_dl)  # (#196): the gate record carries the #175 delta -- selftest_status gates on it
    except Exception as e:
        import traceback; traceback.print_exc()
        _dl = {}
        _deadline = locals().get('_deadline')
        if _deadline is not None:
            _hit = bool(getattr(locals().get('pipe'), 'deadline_hit', False))
            _dl = {'deadline_epoch': _deadline, 'deadline_hit': _hit, 'nondeterministic': _hit}
        write_status(status='error', error=str(e)[:500], **_dl)

if __name__ == '__main__':
    main()
