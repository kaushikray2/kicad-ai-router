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

"""Phase 2E (#66): portfolio racing with fixed-order selection.

Pins the three parts of the feature:

1. Selection (_race_winner / _portfolio_complete) is a pure
   function of the outcomes in fixed candidate order: the first
   COMPLETE candidate (unconnected 0, faults 0) wins -- ahead of
   a cleaner-but-incomplete earlier candidate and ahead of a
   later complete one; with no complete candidate the winner is
   the best _accept_key, ties to the earlier candidate (the
   pre-#66 rule over the same set).
2. The sequential scheduler (workers=1) runs legs in-process in
   order and stops after the first complete one; later legs are
   recorded not-run and are never invoked.
3. ACCEPTANCE (the issue's determinism test): the parallel
   scheduler, driving real child processes through a fake leg
   runner with scripted sleeps, produces the identical winner,
   delivered board and portfolio record across worker counts
   (1 / 2 / 4), finish orders and repeated runs; a leg still
   running when the outcome is decided is terminated and
   recorded out-raced.

The fake runner stands in for race_leg.py (pl.RACE_LEG is
patched): it honours the same contract -- read job.json, write
the board, write leg-result.json atomically. The real child's
end-to-end run is exercised by the #66 bench proof, not here
(it needs the engine + pcbnew).
Run: python3 tests/test_portfolio_racing.py"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl


def _gate(unc, copper=0, banned=0, splits=()):
    # classes carries the fault counts fault_count() sums (the
    # copper_violations field is their precomputed total in a
    # real gate record; the two must agree or the stub lies).
    return {'unconnected_total': unc, 'copper_violations': copper,
            'banned_layer_tracks': banned, 'split_nets': list(splits),
            'classes': {'clearance': copper} if copper else {}}


def _fin(eng, gate):
    return {'engine': eng, 'status': 'finished', 'board': '/x/' + eng,
            'gate': gate, 'key': list(pl._accept_key(gate)), 'seconds': 0.1}


# ---------------------------------------------------------- selection
def test_complete_beats_cleaner_incomplete():
    outs = [_fin('a', _gate(3)), _fin('b', _gate(0)), _fin('c', _gate(0))]
    assert pl._race_winner(outs) == 1


def test_first_complete_wins_over_later_complete():
    outs = [_fin('a', _gate(0)), _fin('b', _gate(0))]
    assert pl._race_winner(outs) == 0


def test_fallback_is_best_key_ties_to_earlier():
    outs = [_fin('a', _gate(5, copper=1)), _fin('b', _gate(2, copper=1)),
            _fin('c', _gate(2, copper=1))]
    # _accept_key is faults-first: b and c tie on (1 fault, 2 unc);
    # the earlier candidate keeps the win, as pre-#66.
    assert pl._race_winner(outs) == 1


def test_unfinished_legs_never_win():
    outs = [{'engine': 'a', 'status': 'failed', 'error': 'x'},
            {'engine': 'b', 'status': 'out-raced'},
            {'engine': 'c', 'status': 'not-run'}]
    assert pl._race_winner(outs) is None
    outs.append(_fin('d', _gate(9)))
    assert pl._race_winner(outs) == 3


def test_complete_definition():
    assert pl._portfolio_complete(_gate(0))
    assert not pl._portfolio_complete(_gate(0, copper=1))
    assert not pl._portfolio_complete(_gate(1))
    assert not pl._portfolio_complete({'copper_violations': 0, 'classes': {}})
    assert not pl._portfolio_complete(None)


# ---------------------------------------------------------- sequential
def _seq_pipe(outcomes_by_eng, workers=1):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('base')
    pipe.cfg = {'portfolio_workers': workers}
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._layer_cost_spec = lambda board: ''
    pipe.calls = []
    def fake_leg(eng):
        pipe.calls.append(eng)
        oc = dict(outcomes_by_eng[eng])
        oc['engine'] = eng
        if oc.get('status') == 'finished':
            p = os.path.join(d, 'pf-%s.kicad_pcb' % eng)
            open(p, 'w').write('board-' + eng)
            oc['board'] = p
        return oc
    pipe._run_leg = fake_leg
    return pipe


def _portfolio_rec(pipe):
    recs = [e for e in pipe.ledger if e['step'] == 'portfolio']
    assert len(recs) == 1, pipe.ledger
    return recs[0]['data']


def test_sequential_stops_after_first_complete():
    outs = {'tm_relax': _fin('tm_relax', _gate(4)),
            'tm_global': _fin('tm_global', _gate(0)),
            'tm_escape': _fin('tm_escape', _gate(0))}
    pipe = _seq_pipe(outs)
    g = pipe.tool_portfolio(['tm_relax', 'tm_global', 'tm_escape'])
    assert pipe.calls == ['tm_relax', 'tm_global'], pipe.calls
    assert g is not None and g['unconnected_total'] == 0
    assert open(pipe.current).read() == 'board-tm_global'
    rec = _portfolio_rec(pipe)
    assert rec['racing']['selection'] == 'first-complete'
    assert rec['racing']['winner_engine'] == 'tm_global'
    terms = {r['engine']: r['termination'] for r in rec['results']}
    assert terms == {'tm_relax': 'finished', 'tm_global': 'finished',
                     'tm_escape': 'not-run'}, terms


def test_sequential_fallback_matches_pre66_winner():
    outs = {'tm_relax': _fin('tm_relax', _gate(5, copper=1)),
            'tm_global': _fin('tm_global', _gate(2, copper=1)),
            'tm_escape': {'engine': 'tm_escape', 'status': 'failed', 'error': 'boom'}}
    pipe = _seq_pipe(outs)
    g = pipe.tool_portfolio(['tm_relax', 'tm_global', 'tm_escape'])
    assert pipe.calls == ['tm_relax', 'tm_global', 'tm_escape'], pipe.calls
    assert open(pipe.current).read() == 'board-tm_global'
    rec = _portfolio_rec(pipe)
    assert rec['racing']['selection'] == 'best-key'
    assert rec['winner'].endswith('pf-tm_global.kicad_pcb')


# ---------------------------------------------------------- parallel
FAKE_RUNNER = '''import json, os, sys, time
sys.path.insert(0, %r)
import pipeline as pl
job = json.load(open(sys.argv[1]))
cfg = job['cfg']
sched = json.load(open(cfg['race_test_schedule']))[job['engine']]
time.sleep(sched.get('sleep', 0))
wd = job['workdir']
if 'gate' in sched:
    board = os.path.join(wd, 'pf-leg.kicad_pcb')
    open(board, 'w').write(sched.get('board_text', 'board-' + job['engine']))
    oc = {'engine': job['engine'], 'status': 'finished', 'board': board,
          'gate': sched['gate'], 'key': list(pl._accept_key(sched['gate'])),
          'seconds': sched.get('sleep', 0)}
else:
    oc = {'engine': job['engine'], 'status': 'failed', 'board': None,
          'error': sched.get('error', 'scripted failure')}
tmp = os.path.join(wd, 'leg-result.json.tmp')
json.dump(oc, open(tmp, 'w'))
os.replace(tmp, os.path.join(wd, 'leg-result.json'))
'''


def _par_pipe(schedule, workers):
    d = tempfile.mkdtemp()
    sched_path = os.path.join(d, 'schedule.json')
    json.dump(schedule, open(sched_path, 'w'))
    runner = os.path.join(d, 'fake_runner.py')
    air = os.path.join(os.path.dirname(__file__), '..', 'ai_router')
    open(runner, 'w').write(FAKE_RUNNER % os.path.abspath(air))
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('base')
    # the child copies input.* out of the parent workdir
    open(os.path.join(d, 'input.kicad_pcb'), 'w').write('input')
    pipe.cfg = {'portfolio_workers': workers, 'race_test_schedule': sched_path}
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._layer_cost_spec = lambda board: ''
    return pipe, runner


def _run_parallel(schedule, workers, engines):
    pipe, runner = _par_pipe(schedule, workers)
    old = pl.RACE_LEG
    pl.RACE_LEG = runner
    try:
        g = pipe.tool_portfolio(engines)
    finally:
        pl.RACE_LEG = old
    return pipe, g


def _sched_a():
    # finish order is the REVERSE of priority: escape first,
    # relax last. relax is incomplete, global complete, escape
    # complete -- the winner must be global at every worker count.
    return {'tm_relax': {'sleep': 0.6, 'gate': _gate(3)},
            'tm_global': {'sleep': 0.3, 'gate': _gate(0)},
            'tm_escape': {'sleep': 0.1, 'gate': _gate(0)}}


def test_parallel_determinism_across_workers_and_repeats():
    engines = ['tm_relax', 'tm_global', 'tm_escape']
    seen = []
    for workers in (4, 2, 4):
        pipe, g = _run_parallel(_sched_a(), workers, engines)
        rec = _portfolio_rec(pipe)
        seen.append((rec['racing']['winner_engine'],
                     open(pipe.current).read(),
                     rec['racing']['selection']))
    assert seen == [('tm_global', 'board-tm_global', 'first-complete')] * 3, seen


def test_parallel_matches_sequential_winner():
    sched = _sched_a()
    pipe, g = _run_parallel(sched, 4, ['tm_relax', 'tm_global', 'tm_escape'])
    outs = {e: _fin(e, s['gate']) for e, s in sched.items()}
    spipe = _seq_pipe(outs)
    sg = spipe.tool_portfolio(['tm_relax', 'tm_global', 'tm_escape'])
    assert open(pipe.current).read() == open(spipe.current).read() == 'board-tm_global'
    assert g['unconnected_total'] == sg['unconnected_total'] == 0


def test_parallel_heat_boundary_skips_later_heats():
    # workers=2: heat 1 = relax+global; global completes, so the
    # second heat (escape) never starts -- no leg dir, not-run.
    pipe, g = _run_parallel(_sched_a(), 2, ['tm_relax', 'tm_global', 'tm_escape'])
    rec = _portfolio_rec(pipe)
    terms = {r['engine']: r['termination'] for r in rec['results']}
    assert terms['tm_escape'] == 'not-run', terms
    assert not os.path.exists(os.path.join(pipe.workdir, 'race-2-tm-escape'))


def test_parallel_outraced_leg_is_terminated():
    sched = {'tm_relax': {'sleep': 0.1, 'gate': _gate(2)},
             'tm_global': {'sleep': 0.2, 'gate': _gate(0)},
             'tm_escape': {'sleep': 30.0, 'gate': _gate(0)}}
    t0 = time.time()
    pipe, g = _run_parallel(sched, 4, ['tm_relax', 'tm_global', 'tm_escape'])
    wall = time.time() - t0
    rec = _portfolio_rec(pipe)
    terms = {r['engine']: r['termination'] for r in rec['results']}
    assert terms['tm_escape'] == 'out-raced', terms
    assert rec['racing']['winner_engine'] == 'tm_global'
    assert wall < 15, wall  # the 30 s leg was killed, not awaited


def test_parallel_fallback_and_failures():
    sched = {'tm_relax': {'sleep': 0.05, 'gate': _gate(5, copper=1)},
             'tm_global': {'sleep': 0.05, 'error': 'engine refused'},
             'tm_escape': {'sleep': 0.05, 'gate': _gate(2, copper=1)}}
    pipe, g = _run_parallel(sched, 4, ['tm_relax', 'tm_global', 'tm_escape'])
    rec = _portfolio_rec(pipe)
    assert rec['racing']['selection'] == 'best-key'
    assert rec['racing']['winner_engine'] == 'tm_escape'
    terms = {r['engine']: r['termination'] for r in rec['results']}
    assert terms['tm_global'] == 'failed', terms
    assert open(pipe.current).read() == 'board-tm_escape'


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('portfolio racing OK: %d tests' % len(fns))
