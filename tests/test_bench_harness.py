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

"""Bench harness (#62) logic tests: inventory + the held-out guard,
the prepared-input cache (byte-stability), DRC caused deltas, the
A/B comparison and the nothing-worse gate, manifest identity.
Pure stdlib: no pcbnew, no pipeline runs."""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from bench_harness import compare as cmp_mod
from bench_harness import drc as drc_mod
from bench_harness import inventory as inv
from bench_harness import prep as prep_mod
from bench_harness import runner


def _mk_dataset(tmp):
    ds = os.path.join(tmp, 'dataset-v3')
    root = os.path.join(tmp, 'boards')
    os.makedirs(ds)
    for st in ('A', 'B'):
        os.makedirs(os.path.join(root, st))
    with open(os.path.join(ds, 'split.json'), 'w') as f:
        json.dump({'train': ['board-a__0', 'board-b__1'],
                   'held_out': ['secret__0']}, f)
    with open(os.path.join(ds, 'dataset.json'), 'w') as f:
        json.dump([{'board': 'board-a__0', 'stratum': 'A'},
                   {'board': 'board-b__1', 'stratum': 'B'},
                   {'board': 'secret__0', 'stratum': 'A'}], f)
    for st, slug in (('A', 'board-a__0'), ('B', 'board-b__1'),
                     ('A', 'secret__0')):
        with open(os.path.join(root, st, slug + '.kicad_pcb'), 'w') as f:
            f.write('(kicad_pcb %s)' % slug)
        with open(os.path.join(root, st, slug + '.kicad_pro'), 'w') as f:
            f.write('{}')
    return ds, root


def test_inventory_splits_and_strata():
    with tempfile.TemporaryDirectory() as tmp:
        ds, root = _mk_dataset(tmp)
        boards = inv.load_dataset_inventory(ds, root)
        by = {b.slug: b for b in boards}
        assert by['board-a__0'].split == 'train'
        assert by['board-a__0'].stratum == 'A'
        assert by['secret__0'].split == 'held_out'
        assert by['board-b__1'].pro.endswith('.kicad_pro')


def test_held_out_guard():
    with tempfile.TemporaryDirectory() as tmp:
        ds, root = _mk_dataset(tmp)
        boards = inv.load_dataset_inventory(ds, root)
        picked = inv.select_boards(boards, ['board-a__0'])
        assert [b.slug for b in picked] == ['board-a__0']
        try:
            inv.select_boards(boards, ['secret__0'])
            raise AssertionError('held-out board was handed out')
        except inv.HeldOutError as e:
            assert 'secret__0' in str(e)
        try:
            inv.select_boards(boards)  # all boards incl. held-out
            raise AssertionError('bulk selection leaked held-out')
        except inv.HeldOutError:
            pass
        picked = inv.select_boards(boards, ['secret__0'], exam=True)
        assert [b.slug for b in picked] == ['secret__0']
        try:
            inv.select_boards(boards, ['nope__9'])
            raise AssertionError('unknown slug accepted')
        except KeyError:
            pass


def _fake_strip(src, out):
    with open(src, 'rb') as f:
        data = f.read()
    with open(out, 'wb') as f:
        f.write(b'STRIPPED:' + data)


def test_prep_cache_byte_stability():
    with tempfile.TemporaryDirectory() as tmp:
        ds, root = _mk_dataset(tmp)
        boards = inv.load_dataset_inventory(ds, root)
        b = inv.select_boards(boards, ['board-a__0'])[0]
        cache = os.path.join(tmp, 'cache')
        m1 = prep_mod.prepare(b, cache, _fake_strip)
        assert m1['cache_hit'] is False
        orig_bytes = open(b.pcb, 'rb').read()
        m2 = prep_mod.prepare(b, cache, _fake_strip)
        assert m2['cache_hit'] is True
        assert m1['prepared_sha256'] == m2['prepared_sha256']
        assert m1['source_key'] == m2['source_key']
        # key is sensitive to the source bytes
        with open(b.pcb, 'a') as f:
            f.write(' ')
        m3 = prep_mod.prepare(b, cache, _fake_strip)
        assert m3['source_key'] != m1['source_key']
        # restore the source bytes, then tamper with the prepared
        # file: the next prepare must refuse the drifted input
        with open(b.pcb, 'wb') as f:
            f.write(orig_bytes)
        with open(m1['prepared_path'], 'ab') as f:
            f.write(b'x')
        try:
            prep_mod.prepare(b, cache, _fake_strip)
            raise AssertionError('tampered prepared input accepted')
        except prep_mod.PrepError:
            pass


def test_drc_parse_and_caused():
    doc = {'violations': [{'type': 'clearance'}, {'type': 'clearance'},
                          {'type': 'track_dangling'}],
           'unconnected_items': [{'items': []}, {'items': []}]}
    s = drc_mod.parse_drc_report(doc)
    assert s['by_kind'] == {'clearance': 2, 'track_dangling': 1}
    assert s['total'] == 3 and s['unconnected'] == 2
    assert s['kinds']['unconnected_items'] == 2
    d = drc_mod.caused_deltas({'clearance': 3, 'new_kind': 1},
                              {'clearance': 2, 'track_dangling': 4})
    assert d == {'clearance': 1, 'new_kind': 1, 'track_dangling': -4}
    assert drc_mod.caused_increases(d) == {'clearance': 1, 'new_kind': 1}


def _rec(slug, unc, splits, copper, caused, wall=10.0):
    return {'slug': slug, 'status': 'done', 'wall_s': wall,
            'gate': {'unconnected': unc, 'splits': splits,
                     'copper': copper}, 'caused': caused}


def test_compare_table_and_gate():
    a = {'manifest': {'stack': {'name': 'A'}}, 'records': {
        'b1': _rec('b1', 2, 0, 1, {'clearance': 1}),
        'b2': _rec('b2', 0, 0, 0, {})}}
    b = {'manifest': {'stack': {'name': 'B'}}, 'records': {
        'b1': _rec('b1', 2, 0, 1, {'clearance': 1}),
        'b2': _rec('b2', 0, 0, 0, {})}}
    comp = cmp_mod.compare_runs(a, b)
    assert comp['aggregate']['ties'] == 2
    assert comp['aggregate']['unconnected_a'] == 2
    md = cmp_mod.render_compare_markdown(comp)
    assert 'b1' in md and '2/0/1' in md
    gate = cmp_mod.nothing_worse(b['records'], a['records'])
    assert gate['ok']
    # a caused-kind regression fails the gate even with equal triples
    b['records']['b1'] = _rec('b1', 2, 0, 1, {'clearance': 2})
    gate = cmp_mod.nothing_worse(b['records'], a['records'])
    assert not gate['ok']
    assert gate['regressions'][0]['kind'] == 'clearance'
    # a triple regression fails it too
    b['records']['b1'] = _rec('b1', 3, 0, 1, {'clearance': 1})
    gate = cmp_mod.nothing_worse(b['records'], a['records'])
    assert not gate['ok']
    assert gate['regressions'][0]['kind'] == 'gate.unconnected'
    # a board with no baseline record is a regression entry
    b['records']['b3'] = _rec('b3', 0, 0, 0, {})
    gate = cmp_mod.nothing_worse(b['records'], a['records'])
    assert not gate['ok']
    # raw pre-existing faults do not gate: only caused deltas count
    b['records'] = {'b1': _rec('b1', 2, 0, 1, {'clearance': 0}),
                    'b2': _rec('b2', 0, 0, 0, {})}
    assert cmp_mod.nothing_worse(b['records'], a['records'])['ok']


def test_manifest_identity():
    cfg1 = {'tracemaker_path': '/nonexistent/tm', 'tm_work': 40000000}
    cfg2 = {'tm_work': 40000000, 'tracemaker_path': '/nonexistent/tm'}
    assert runner.cfg_fingerprint(cfg1) == runner.cfg_fingerprint(cfg2)
    cfg3 = dict(cfg1, tm_work=20000000)
    assert runner.cfg_fingerprint(cfg3) != runner.cfg_fingerprint(cfg1)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b'engine-bytes')
        path = f.name
    try:
        ident = runner.engine_identity({'tracemaker_path': path})
        assert ident['sha256'] == prep_mod.sha256_file(path)
        assert runner.repo_version()
        man = runner.build_manifest(
            'run-x', 'stack', {}, {'tracemaker_path': path},
            [(inv.Board('s__0', 'train', 'A', '/x.kicad_pcb'),
              {'source_pcb_sha256': 'aa', 'prepared_sha256': 'bb',
               'cache_hit': True})], exam=False)
        assert man['exam'] is False
        assert man['boards'][0]['prepared_sha256'] == 'bb'
        assert man['engine']['sha256'] == ident['sha256']
    finally:
        os.unlink(path)



def test_nothing_worse_fails_crashed_candidate():
    # #223: a crashed candidate record (status=error, no gate, no
    # caused) must FAIL the nothing-worse gate, not pass it by
    # treating every missing value as zero / skipping the checks.
    base = {'b1': _rec('b1', 2, 0, 1, {'clearance': 1})}
    crashed = {'slug': 'b1', 'status': 'error',
               'error': 'RuntimeError: boom'}
    gate = cmp_mod.nothing_worse({'b1': crashed}, base)
    assert not gate['ok'], gate
    # status=done but nothing measured (no final board): also a fail
    hollow = {'slug': 'b1', 'status': 'done'}
    gate = cmp_mod.nothing_worse({'b1': hollow}, base)
    assert not gate['ok'], gate
    # a gate triple with a None component is missing data, not a pass
    nones = _rec('b1', None, 0, 1, {'clearance': 1})
    gate = cmp_mod.nothing_worse({'b1': nones}, base)
    assert not gate['ok'], gate
    # a crashed baseline cannot certify a candidate either
    gate = cmp_mod.nothing_worse(base, {'b1': crashed})
    assert not gate['ok'], gate


def test_compare_aggregates_surface_missing_and_error():
    # #223: aggregates must count missing/error records explicitly
    # instead of summing only over records with a gate (which made
    # crashed boards lower the totals).
    a = {'manifest': {'stack': {'name': 'A'}}, 'records': {
        'b1': _rec('b1', 2, 0, 1, {'clearance': 1})}}
    b = {'manifest': {'stack': {'name': 'B'}}, 'records': {
        'b1': {'slug': 'b1', 'status': 'error',
               'error': 'RuntimeError: boom'}}}
    comp = cmp_mod.compare_runs(a, b)
    agg = comp['aggregate']
    assert agg['error_b'] == 1 and agg['missing_b'] == 1, agg
    assert agg['error_a'] == 0 and agg['missing_a'] == 0, agg
    md = cmp_mod.render_compare_markdown(comp)
    assert 'error' in md.lower() and 'missing' in md.lower(), md


def test_crashed_candidate_fails_compare_gate_from_disk():
    # #223: the exact calls cmd_compare --gate makes, over run
    # directories on disk: baseline complete, candidate crashed.
    with tempfile.TemporaryDirectory() as tmp:
        def _write_run(name, rec):
            rd = os.path.join(tmp, name)
            bd = os.path.join(rd, 'boards', rec['slug'])
            os.makedirs(bd)
            with open(os.path.join(rd, 'manifest.json'), 'w') as f:
                json.dump({'stack': {'name': name}}, f)
            with open(os.path.join(bd, 'record.json'), 'w') as f:
                json.dump(rec, f)
            return rd
        good = _rec('b1', 2, 0, 1, {'clearance': 1})
        crashed = {'slug': 'b1', 'status': 'error',
                   'error': 'RuntimeError: boom'}
        ra = cmp_mod.load_run(_write_run('base', good))
        rb = cmp_mod.load_run(_write_run('cand', crashed))
        gate = cmp_mod.nothing_worse(rb['records'], ra['records'])
        assert not gate['ok'], gate



def test_resume_guard_checks_engine_and_git_identity():
    # #224: a complete record may only be skipped when the engine
    # binary and the plugin code that produced it are the ones the
    # resumed run would use.  The old guard matched prepared input
    # + cfg fingerprint only, and the fingerprint hashes the
    # tracemaker_path STRING -- an in-place engine rebuild or a
    # plugin update silently mixed stale records into the run.
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = os.path.join(tmp, 'run')
        board = inv.Board('b__0', 'train', 'A', '/x.kicad_pcb')
        prep_file = os.path.join(tmp, 'prepared.kicad_pcb')
        with open(prep_file, 'w') as f:
            f.write('(kicad_pcb fake)')
        meta = {'prepared_sha256': 'pp', 'source_pcb_sha256': 'ss',
                'prepared_path': prep_file, 'cache_hit': True}
        bdir = os.path.join(run_dir, 'boards', 'b__0')
        os.makedirs(bdir)
        old = {'slug': 'b__0', 'status': 'done',
               'prepared_sha256': 'pp', 'cfg_fingerprint': 'fp',
               'engine_sha256': 'ENG-A', 'git_sha': 'GIT-A',
               'gate': {'unconnected': 0, 'splits': 0, 'copper': 0}}
        def _restore():
            with open(os.path.join(bdir, 'record.json'), 'w') as f:
                json.dump(old, f)
        _restore()
        logs = []
        got = runner.run_board(board, meta, 'stack', {}, 'fp',
                               run_dir, logs.append,
                               engine_sha256='ENG-A', git_sha='GIT-A')
        assert got['status'] == 'done' and got['gate'] == old['gate'], got
        assert any('skip' in m for m in logs), logs
        # a rebuilt engine at the same path: NOT skipped -- the
        # board re-runs (and errors on the fake board file, which
        # proves the skip did not happen)
        _restore()
        got = runner.run_board(board, meta, 'stack', {}, 'fp',
                               run_dir, logs.append,
                               engine_sha256='ENG-B', git_sha='GIT-A')
        assert got['status'] == 'error', got
        assert got.get('engine_sha256') == 'ENG-B', got
        # a plugin code update: NOT skipped either
        _restore()
        got = runner.run_board(board, meta, 'stack', {}, 'fp',
                               run_dir, logs.append,
                               engine_sha256='ENG-A', git_sha='GIT-B')
        assert got['status'] == 'error', got
        assert got.get('git_sha') == 'GIT-B', got



def test_effective_cfg_work_key_and_unknown_key_warning():
    # #225(a): the documented budget key is 'work' (config.DEFAULTS);
    # an unknown override key like the old README's 'tm_work' must
    # warn loudly instead of silently changing only the fingerprint.
    cfg = runner.effective_cfg({'work': 123})
    assert cfg['work'] == 123
    err = io.StringIO()
    old_err = sys.stderr
    sys.stderr = err
    try:
        runner.effective_cfg({'tm_work': 40000000})
    finally:
        sys.stderr = old_err
    assert 'tm_work' in err.getvalue(), err.getvalue()


def test_effective_cfg_tool_paths_from_load():
    # #225(b): machine tool paths come from config.load() when not
    # overridden -- DEFAULTS' tracemaker_path is a stale path from
    # 2026-11-12.  Other values stay at DEFAULTS (only the tool
    # paths are machine state).
    from ai_router import config as cfgmod
    with tempfile.TemporaryDirectory() as tmp:
        cpath = os.path.join(tmp, 'config.json')
        with open(cpath, 'w') as f:
            json.dump({'tracemaker_path': '/machine/tm',
                       'kicad_cli': '/machine/kicad-cli',
                       'work': 7}, f)
        old_path = cfgmod.CONFIG_PATH
        cfgmod.CONFIG_PATH = cpath
        try:
            cfg = runner.effective_cfg()
            assert cfg['tracemaker_path'] == '/machine/tm', cfg
            assert cfg['kicad_cli'] == '/machine/kicad-cli', cfg
            assert cfg['work'] == cfgmod.DEFAULTS['work'], cfg
            cfg = runner.effective_cfg({'tracemaker_path': '/ovr/tm'})
            assert cfg['tracemaker_path'] == '/ovr/tm', cfg
        finally:
            cfgmod.CONFIG_PATH = old_path


def test_kicad_drc_uses_cfg_kicad_cli():
    # #225(c): the DRC yardstick must use the configured kicad_cli,
    # not a hardcoded 'kicad-cli'.
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        class _P(object):
            returncode = 0
            stdout = ''
            stderr = ''
        def fake_run(cmd, **kw):
            calls.append(cmd)
            with open(cmd[cmd.index('--output') + 1], 'w') as f:
                f.write('{}')
            return _P()
        old_run = runner.subprocess.run
        runner.subprocess.run = fake_run
        try:
            runner.kicad_drc('b.kicad_pcb',
                             os.path.join(tmp, 'o.json'),
                             kicad_cli='/machine/kicad-cli')
        finally:
            runner.subprocess.run = old_run
        assert calls and calls[0][0] == '/machine/kicad-cli', calls


def test_worker_op_never_reads_stale_result():
    # #225(d): a .result.json left by an earlier invocation must
    # never be read as this invocation's result.  A result written
    # by THIS invocation is trusted even when the worker exits
    # non-zero afterwards (pcbnew can segfault at interpreter exit
    # after successful work -- Pipeline._worker ignores rc too).
    with tempfile.TemporaryDirectory() as tmp:
        board = os.path.join(tmp, 'b.kicad_pcb')
        with open(board, 'w') as f:
            f.write('(kicad_pcb fake)')
        with open(board + '.result.json', 'w') as f:
            json.dump({'stale': True}, f)
        class _P(object):
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = ''
                self.stderr = 'boom'
        old_run = runner.subprocess.run
        runner.subprocess.run = lambda cmd, **kw: _P(1)
        try:
            try:
                got = runner.worker_op('strip', board)
                raise AssertionError('stale result was trusted: %r'
                                     % (got,))
            except RuntimeError:
                pass
            def fake_fresh(cmd, **kw):
                res = cmd[cmd.index('--result') + 1]
                with open(res, 'w') as f:
                    json.dump({'fresh': 1}, f)
                return _P(1)
            runner.subprocess.run = fake_fresh
            got = runner.worker_op('strip', board)
            assert got == {'fresh': 1}, got
        finally:
            runner.subprocess.run = old_run


if __name__ == '__main__':
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith('test_') and callable(f)]
    for n, f in fns:
        f()
        print('PASS %s' % n)
    print('PASS test_bench_harness (%d tests)' % len(fns))
