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


if __name__ == '__main__':
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith('test_') and callable(f)]
    for n, f in fns:
        f()
        print('PASS %s' % n)
    print('PASS test_bench_harness (%d tests)' % len(fns))
