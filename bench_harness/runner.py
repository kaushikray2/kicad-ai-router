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

"""Single-stack bench runs (#62).

For each selected board: take the cached prepared input, run the
plugin Pipeline in AUTO mode on it (in-process, the same construction
the dialog and the backend use), then measure the delivered board
with KiCad DRC and diff it against the prepared input's DRC -- the
routing-caused delta the nothing-worse gate scores. Each board lands
as one record JSON under the run directory; a run also carries a
manifest (exact config + code/engine identity) and a summary, so
runs are resumable (a board with a complete record for the same
prepared input and config is skipped) and comparable across stacks.
Runs are plain CLI work: launch them detached for long sets.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

from . import drc as drc_mod
from . import prep as prep_mod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(REPO, 'ai_router', 'worker.py')


def sha256_file(path):
    return prep_mod.sha256_file(path)


def repo_version():
    try:
        txt = open(os.path.join(REPO, 'build_package.py')).read()
        m = re.search(r"^VERSION = '([^']+)'", txt, re.M)
        return m.group(1) if m else 'unknown'
    except Exception:
        return 'unknown'


def git_sha():
    try:
        p = subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'],
                           capture_output=True, text=True, timeout=30)
        return p.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def worker_op(op, board, out=None):
    """Run one ai_router worker op exactly as Pipeline._worker does."""
    result = (out or board) + '.result.json'
    cmd = [sys.executable, WORKER, op, '--board', board]
    if out:
        cmd += ['--out', out]
    cmd += ['--result', result]
    # #225(d): never let a result file from an EARLIER invocation
    # be read as this one's -- the path is deterministic, unlike
    # Pipeline._worker's per-call sequence-numbered path.  The
    # return code is deliberately NOT gated on: worker.py writes
    # its result before exiting and pcbnew can segfault at
    # interpreter exit after successful work (Pipeline._worker
    # ignores rc for the same reason); after the unlink, a result
    # file that exists can only be this invocation's.
    if os.path.exists(result):
        os.unlink(result)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if os.path.exists(result):
        with open(result) as f:
            return json.load(f)
    lines = [l for l in p.stdout.splitlines() if l.startswith('AIR_RESULT ')]
    if lines:
        return json.loads(lines[-1][len('AIR_RESULT '):])
    raise RuntimeError('worker %s failed: %s'
                       % (op, (p.stderr or '')[-300:]))


def strip_board(src, out):
    """The pipeline's own strip (worker cmd_strip) -- the prepared
    input is byte-identical to the board the pipeline starts from."""
    return worker_op('strip', src, out)


def kicad_drc(board, out_json, kicad_cli='kicad-cli'):
    """KiCad DRC with zone refill: the yardstick invocation the
    pipeline's own gate uses (Pipeline._drc_measure)."""
    p = subprocess.run([kicad_cli, 'pcb', 'drc', '--refill-zones',
                        '--format', 'json', '--output', out_json, board],
                       capture_output=True, text=True, timeout=900)
    if p.returncode != 0 or not os.path.exists(out_json):
        raise RuntimeError('kicad-cli DRC failed (rc=%s) on %s: %s'
                           % (p.returncode, os.path.basename(board),
                              (p.stderr or '')[-300:]))
    with open(out_json) as f:
        return json.load(f)


# cfg keys the pipeline/orchestrator read via cfg.get() with their
# own defaults, beyond config.DEFAULTS (grepped 2026-11-26).
KNOWN_EXTRA_CFG_KEYS = frozenset({'first_nets_policy'})


def effective_cfg(overrides=None):
    from ai_router import config as cfgmod
    cfg = dict(cfgmod.DEFAULTS)
    overrides = overrides or {}
    # #225(b): machine tool paths are machine state, not stack
    # state -- take them from the persisted config when the stack
    # does not override them.  DEFAULTS' tracemaker_path is a
    # stale 2026-11-12 path; a bench that silently used it would
    # measure a different engine than the manifest claims.
    loaded = cfgmod.load()
    for k in ('tracemaker_path', 'kicad_cli'):
        if k not in overrides:
            cfg[k] = loaded.get(k, cfg[k])
    # #225(a): an override key the pipeline does not define (the
    # old README example 'tm_work' for the real key 'work') used
    # to change the cfg fingerprint while changing nothing else
    # -- a bench that silently ran the default budget.  Warn
    # loudly instead.
    unknown = sorted(k for k in overrides
                     if k not in cfgmod.DEFAULTS
                     and k not in KNOWN_EXTRA_CFG_KEYS)
    if unknown:
        sys.stderr.write(
            'bench_harness: WARNING: unknown cfg override key(s) '
            '%s -- not pipeline config keys; they change the cfg '
            'fingerprint but nothing else.  Config keys live in '
            'ai_router.config.DEFAULTS (the work budget key is '
            "'work').\n" % ', '.join(unknown))
    cfg.update(overrides)
    cfg['mode'] = 'auto'
    return cfg


def cfg_fingerprint(cfg):
    blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def engine_identity(cfg):
    path = cfg.get('tracemaker_path')
    if path and os.path.exists(path):
        return {'path': path, 'sha256': sha256_file(path)}
    return {'path': path, 'sha256': None}


def _stem(path):
    return path[:-len('.kicad_pcb')] if path.endswith('.kicad_pcb') else path


def run_board(board, prep_meta, stack_name, cfg, fingerprint, run_dir, log,
              engine_sha256=None, git_sha=None):
    """Run one board under one stack; returns its record dict."""
    bdir = os.path.join(run_dir, 'boards', board.slug)
    os.makedirs(bdir, exist_ok=True)
    rec_path = os.path.join(bdir, 'record.json')
    if os.path.exists(rec_path):
        with open(rec_path) as f:
            old = json.load(f)
        # #224: the skip must also match WHO produced the record --
        # the engine binary (by sha256, not the path string the cfg
        # fingerprint hashes) and the plugin git SHA.  An in-place
        # engine rebuild or a code update otherwise resumes over
        # stale records under a manifest claiming the new identity.
        if (old.get('status') == 'done'
                and old.get('prepared_sha256') == prep_meta['prepared_sha256']
                and old.get('cfg_fingerprint') == fingerprint
                and old.get('engine_sha256') == engine_sha256
                and old.get('git_sha') == git_sha):
            log('skip %s (complete record, same input + config + '
                'engine + code)' % board.slug)
            return old
    staged = os.path.join(bdir, 'board.kicad_pcb')
    shutil.copy2(prep_meta['prepared_path'], staged)
    for ext in ('.kicad_pro', '.kicad_dru'):
        sib = _stem(prep_meta['prepared_path']) + ext
        if os.path.exists(sib):
            shutil.copy2(sib, _stem(staged) + ext)
    rec = {'slug': board.slug, 'split': board.split,
           'stratum': board.stratum, 'stack': stack_name,
           'cfg_fingerprint': fingerprint,
           'source_pcb_sha256': prep_meta['source_pcb_sha256'],
           'prepared_sha256': prep_meta['prepared_sha256'],
           'prep_cache_hit': prep_meta.get('cache_hit', False),
           'engine_sha256': engine_sha256,
           'git_sha': git_sha,
           'status': 'running'}
    lines = []

    def _log(m):
        lines.append(str(m))
        log(str(m))

    t0 = time.time()
    try:
        in_doc = kicad_drc(staged, os.path.join(bdir, 'input.drc.json'),
                           kicad_cli=cfg.get('kicad_cli') or 'kicad-cli')
        rec['input_drc'] = drc_mod.parse_drc_report(in_doc)
        from ai_router import orchestrator
        from ai_router.pipeline import Pipeline
        pipe = Pipeline(cfg, staged, _log)
        res = orchestrator.run_auto(cfg, pipe, _log)
        rec['wall_s'] = round(time.time() - t0, 1)
        rec['workdir'] = pipe.workdir
        g = res.get('gate') or {}
        split_nets = g.get('split_nets')
        # the gate record carries split_nets as a LIST of net names;
        # the bench triple counts them (the selftest convention).
        splits = (len(split_nets) if isinstance(split_nets, (list, tuple))
                  else split_nets)
        rec['gate'] = {'unconnected': g.get('unconnected_total'),
                       'splits': splits,
                       'copper': g.get('copper_violations')}
        rec['gate_raw'] = {k: g.get(k) for k in (
            'unconnected_total', 'split_nets', 'split_signal_nets',
            'split_pour_nets', 'violations_total', 'copper_violations',
            'vias', 'wirelength_mm')}
        final = res.get('final_board')
        rec['final_board'] = final
        # #223: a run that produced no final board measured
        # nothing -- it is an error record, not a 'done' record
        # with the measurement blocks silently absent (the
        # nothing-worse gate reads a missing caused/gate as a
        # failed board, but the record must not claim success).
        if not (final and os.path.exists(final)):
            raise RuntimeError(
                'pipeline produced no final board (final_board=%r)'
                % (final,))
        out_copy = os.path.join(bdir, 'result.kicad_pcb')
        shutil.copy2(final, out_copy)
        pro = os.path.join(pipe.workdir, 'input.kicad_pro')
        if os.path.exists(pro):
            shutil.copy2(pro, os.path.join(bdir, 'result.kicad_pro'))
        dru = os.path.join(pipe.workdir, 'input.kicad_dru')
        if os.path.exists(dru):
            shutil.copy2(dru, os.path.join(bdir, 'result.kicad_dru'))
        out_doc = kicad_drc(out_copy,
                            os.path.join(bdir, 'output.drc.json'),
                            kicad_cli=cfg.get('kicad_cli')
                            or 'kicad-cli')
        rec['output_drc'] = drc_mod.parse_drc_report(out_doc)
        rec['caused'] = drc_mod.caused_deltas(
            rec['output_drc']['kinds'], rec['input_drc']['kinds'])
        rec['drift'] = {
            'gate_violations': g.get('violations_total'),
            'kicad_violations': rec['output_drc']['total'],
            'gate_unconnected': g.get('unconnected_total'),
            'kicad_unconnected': rec['output_drc']['unconnected']}
        led = os.path.join(pipe.workdir, 'ledger.json')
        if os.path.exists(led):
            shutil.copy2(led, os.path.join(bdir, 'ledger.json'))
            rec['ledger'] = os.path.join(bdir, 'ledger.json')
        rec['status'] = 'done'
    except Exception as e:
        rec['wall_s'] = round(time.time() - t0, 1)
        rec['status'] = 'error'
        rec['error'] = '%s: %s' % (type(e).__name__, e)
    with open(os.path.join(bdir, 'run.log'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    with open(rec_path, 'w') as f:
        json.dump(rec, f, indent=1)
    return rec


def build_manifest(run_id, stack_name, overrides, cfg, boards_prep,
                   exam=False):
    return {
        'run_id': run_id, 'created_utc': time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'host': socket.gethostname(), 'exam': bool(exam),
        'git_sha': git_sha(), 'version': repo_version(),
        'engine': engine_identity(cfg),
        'stack': {'name': stack_name, 'overrides': overrides or {}},
        'cfg_fingerprint': cfg_fingerprint(cfg),
        'boards': [{'slug': b.slug, 'split': b.split,
                    'stratum': b.stratum,
                    'source_pcb_sha256': p['source_pcb_sha256'],
                    'prepared_sha256': p['prepared_sha256'],
                    'prep_cache_hit': p.get('cache_hit', False)}
                   for b, p in boards_prep]}


def run_set(boards, stack_name, overrides, home, run_id, exam=False,
            log=print):
    """Prepare (cached) and run a board set under one stack.

    Returns (run_dir, manifest, records). The manifest is written
    before the first board runs, so a detached run is inspectable
    mid-flight; summary.json/summary.md land when the set finishes.
    """
    from . import compare as compare_mod
    cache_dir = os.path.join(home, 'cache')
    run_dir = os.path.join(home, 'runs', run_id)
    os.makedirs(run_dir, exist_ok=True)
    cfg = effective_cfg(overrides)
    fingerprint = cfg_fingerprint(cfg)
    boards_prep = []
    for b in boards:
        meta = prep_mod.prepare(b, cache_dir, strip_board)
        boards_prep.append((b, meta))
        log('prep %s: %s (cache %s)' % (
            b.slug, meta['prepared_sha256'][:12],
            'hit' if meta.get('cache_hit') else 'miss'))
    manifest = build_manifest(run_id, stack_name, overrides, cfg,
                              boards_prep, exam=exam)
    manifest['state'] = 'running'
    with open(os.path.join(run_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    records = {}
    eng_sha = engine_identity(cfg)['sha256']
    g_sha = git_sha()
    for b, meta in boards_prep:
        log('run %s under stack %s' % (b.slug, stack_name))
        rec = run_board(b, meta, stack_name, cfg, fingerprint, run_dir,
                        log, engine_sha256=eng_sha, git_sha=g_sha)
        records[b.slug] = rec
        log('done %s: %s gate=%s wall=%ss' % (
            b.slug, rec['status'], rec.get('gate'), rec.get('wall_s')))
    manifest['state'] = 'complete'
    with open(os.path.join(run_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(run_dir, 'summary.json'), 'w') as f:
        json.dump({'manifest': manifest, 'records': records}, f, indent=1)
    with open(os.path.join(run_dir, 'summary.md'), 'w') as f:
        f.write(compare_mod.render_run_summary(records, manifest))
    return run_dir, manifest, records
