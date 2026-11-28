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

"""Phase 2E item 5 (#61): best-so-far output + shared deadline
semantics for backend routing runs.

Pins:
1. The run key (_final_key / _unc_key) has ONE definition, in
   pipeline.py; the orchestrator imports it -- the checkpoint
   hook prices candidate boards with exactly the key the AUTO
   policy's own best-tracking uses, so the artifact a killed run
   leaves is the board the run itself considered best.
2. Deadline resolution: cfg['deadline_epoch'] (one absolute point
   in time) wins; the AIR_DEADLINE_EPOCH env var is the channel
   subprocess workers inherit; neither means no deadline, and a
   deadline that has passed marks the run (deadline_hit).
3. The checkpoint hook: writes the output artifact only on a
   strict improvement of the run key, atomically (tmp beside the
   target + replace, no litter), with the board's sidecars; a
   no-op when no output path is configured (local runs) or no
   gate priced the board. The best-key state can be shared
   across Pipeline objects (the layer-law escalation variants),
   so a later variant cannot overwrite an earlier variant's
   better board with a worse one.
4. Work refusal: pipeline tools raise DeadlinePassed instead of
   STARTING new work after the deadline; a portfolio leg
   resolves as not-run/deadline data (the #66 contract: a leg
   outcome is data, never an exception); the gate -- pure
   measurement, which the checkpoint and the final record
   depend on -- is never refused.
5. Backend wiring: run_job.build_cfg carries time_limit_s
   (plugin DEFAULTS are the single source; defensive parse);
   run_job converts the limit ONCE into the absolute deadline;
   the result copy is atomic; the server's default config and
   its config POST whitelist carry the key.
Run: python3 tests/test_best_so_far.py"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'docker'))
import pipeline as pl


def _gate(unc, copper=0, banned=0, splits=()):
    return {'unconnected_total': unc, 'copper_violations': copper,
            'banned_layer_tracks': banned, 'split_nets': list(splits),
            'classes': {'clearance': copper} if copper else {}}


def _shell(cfg=None, workdir=None):
    # A Pipeline shell with just the state the #61 machinery
    # touches -- the race_leg.py construction pattern.
    pipe = object.__new__(pl.Pipeline)
    pipe.cfg = dict(cfg or {})
    pipe.board_path = os.path.join(workdir, 'input.kicad_pcb')
    pipe.workdir = workdir
    pipe.base = os.path.join(workdir, 'base.kicad_pcb')
    pipe.current = pipe.base
    pipe.pass_no = 0
    pipe._oseq = 0; pipe._wseq = 0; pipe._gseq = 0
    pipe.ledger = []
    pipe.last_gate = None
    pipe.last_route = None
    pipe.log = lambda m: None
    return pipe


def _board(wd, name, data):
    p = os.path.join(wd, name)
    with open(p, 'w') as f: f.write(data)
    return p


def test_final_key_single_source():
    import orchestrator as orc
    assert orc._final_key is pl._final_key
    assert orc._unc_key is pl._unc_key
    g = lambda unc, splits=(): {'unconnected_total': unc, 'split_nets': list(splits), 'classes': {}}
    assert pl._final_key(g(0)) < pl._final_key(g(1))          # unconnected first
    assert pl._final_key(g(1)) < pl._final_key(g(1, ('A',)))  # then splits
    assert pl._unc_key({'unconnected_total': None}) == 10 ** 9  # None is worst (#13)


def test_deadline_resolution():
    wd = tempfile.mkdtemp()
    now = time.time()
    p = _shell({'deadline_epoch': now + 3600}, wd)
    assert p._deadline_epoch() == now + 3600
    assert p.deadline_passed() is False
    assert getattr(p, 'deadline_hit', False) is False
    p2 = _shell({'deadline_epoch': now - 5}, wd)
    assert p2.deadline_passed() is True
    assert p2.deadline_hit is True
    p3 = _shell({}, wd)
    assert p3._deadline_epoch() is None
    assert p3.deadline_passed() is False
    os.environ[pl.DEADLINE_ENV] = repr(now - 5)
    try:
        p4 = _shell({}, wd)
        assert p4.deadline_passed() is True       # the inherited channel
        p5 = _shell({'deadline_epoch': now + 3600}, wd)
        assert p5.deadline_passed() is False      # cfg wins over env
    finally:
        del os.environ[pl.DEADLINE_ENV]
    p6 = _shell({'deadline_epoch': 'soon'}, wd)   # garbage never crashes
    assert p6._deadline_epoch() is None and p6.deadline_passed() is False


def test_checkpoint_keeps_best_only():
    wd = tempfile.mkdtemp()
    p = _shell({}, wd)
    out = os.path.join(wd, 'result.kicad_pcb')
    p.best_so_far_path = out
    bad = _board(wd, 'b1.kicad_pcb', 'board-bad')
    good = _board(wd, 'b2.kicad_pcb', 'board-good')
    mid = _board(wd, 'b3.kicad_pcb', 'board-mid')
    p._checkpoint(bad, _gate(9))
    assert open(out).read() == 'board-bad'        # first candidate always lands
    p._checkpoint(good, _gate(0))
    assert open(out).read() == 'board-good'       # an improvement replaces it
    p._checkpoint(mid, _gate(4))
    assert open(out).read() == 'board-good'       # a worse board never does
    p._checkpoint(good, _gate(0))
    assert open(out).read() == 'board-good'       # an equal key does not rewrite
    left = [f for f in os.listdir(wd) if '.tmp' in f]
    assert not left, left                         # atomic writes leave no litter
    recs = [e for e in p.ledger if e['step'] == 'best_so_far']
    assert len(recs) == 2, recs                   # only the improvements recorded


def test_checkpoint_sidecars_and_noops():
    wd = tempfile.mkdtemp()
    p = _shell({}, wd)
    bp = _board(wd, 'cur.kicad_pcb', 'board')
    _board(wd, 'cur.kicad_pro', 'pro-data')
    out = os.path.join(wd, 'result.kicad_pcb')
    p.best_so_far_path = out
    p._checkpoint(bp, None)                       # no gate priced it: no write
    assert not os.path.exists(out)
    p._checkpoint(os.path.join(wd, 'missing.kicad_pcb'), _gate(0))
    assert not os.path.exists(out)                # a vanished board: no write
    p._checkpoint(bp, _gate(3))
    assert open(out).read() == 'board'
    # the sidecar travels, so the artifact is gate-checkable standalone
    assert open(os.path.join(wd, 'result.kicad_pro')).read() == 'pro-data'
    p2 = _shell({}, wd)                           # no output path (local runs)
    p2._checkpoint(bp, _gate(0))                  # a silent no-op, never a crash


def test_checkpoint_state_shared_across_pipes():
    # run_auto's layer-law escalation builds a second Pipeline;
    # with the best-key state shared (what the escalation wiring
    # does), the second pipe cannot overwrite the first pipe's
    # better board with a worse one.
    wd = tempfile.mkdtemp()
    out = os.path.join(wd, 'result.kicad_pcb')
    a = _shell({}, wd); a.best_so_far_path = out
    b = _shell({}, wd); b.best_so_far_path = out
    good = _board(wd, 'a.kicad_pcb', 'board-A')
    worse = _board(wd, 'b.kicad_pcb', 'board-B')
    ga = _gate(0); ga['cost_weighted_wirelength_mm'] = 100.0
    a._checkpoint(good, ga)
    assert open(out).read() == 'board-A'
    b._best_state = a._best_state
    b._checkpoint(worse, _gate(7))
    assert open(out).read() == 'board-A'          # a worse board never overwrites
    a._checkpoint(good, ga)
    assert open(out).read() == 'board-A'          # an equal key does not rewrite
    better = _board(wd, 'b2.kicad_pcb', 'board-B2')
    gb = _gate(0); gb['cost_weighted_wirelength_mm'] = 50.0
    b._checkpoint(better, gb)
    assert open(out).read() == 'board-B2'         # a real improvement still lands


def test_tools_refuse_new_work_after_deadline():
    wd = tempfile.mkdtemp()
    p = _shell({'deadline_epoch': time.time() - 1}, wd)
    calls = (lambda: p.tool_route(), lambda: p.tool_portfolio(['tm_relax']),
             lambda: p.tool_finish_leg('NET'), lambda: p.tool_long_finish('NET'),
             lambda: p.tool_stitch(), lambda: p.tool_heal(), lambda: p.tool_prune(),
             lambda: p.tool_fix_violations(), lambda: p.tool_ilp_repair(['NET']))
    for call in calls:
        try:
            call()
        except pl.DeadlinePassed:
            pass
        else:
            raise AssertionError('expected DeadlinePassed from %r' % (call,))
    # a portfolio leg resolves as data, never an exception (#66)
    oc = p._run_leg('tm_relax')
    assert oc['status'] == 'not-run' and oc.get('detail') == 'deadline', oc
    # the gate is measurement -- the checkpoint and the final
    # record depend on it -- so it is never deadline-refused; on
    # a missing board it fails for its own reason only.
    try:
        p.tool_gate(os.path.join(wd, 'nope.kicad_pcb'))
    except pl.DeadlinePassed:
        raise AssertionError('tool_gate must not refuse on deadline')
    except Exception:
        pass


def test_build_cfg_time_limit():
    import run_job
    assert run_job.build_cfg({}, {}, '/tm')['time_limit_s'] == 0
    assert run_job.build_cfg({'time_limit_s': 900}, {}, '/tm')['time_limit_s'] == 900
    assert run_job.build_cfg({'time_limit_s': 'abc'}, {}, '/tm')['time_limit_s'] == 0


def test_deadline_epoch_conversion():
    import run_job
    # the limit (a duration) becomes ONE absolute point in time
    assert run_job._deadline_epoch_for({'time_limit_s': 0}, 1000.0) is None
    assert run_job._deadline_epoch_for({}, 1000.0) is None
    assert run_job._deadline_epoch_for({'time_limit_s': -5}, 1000.0) is None
    assert run_job._deadline_epoch_for({'time_limit_s': 90}, 1000.0) == 1090.0


def test_atomic_copy():
    import run_job
    wd = tempfile.mkdtemp()
    src = os.path.join(wd, 'a'); dst = os.path.join(wd, 'b')
    open(src, 'w').write('one'); run_job._atomic_copy(src, dst)
    assert open(dst).read() == 'one'
    open(src, 'w').write('two'); run_job._atomic_copy(src, dst)
    assert open(dst).read() == 'two'              # always a complete file
    assert not [f for f in os.listdir(wd) if '.tmp' in f]


def test_server_config_carries_time_limit():
    import importlib.machinery, importlib.util
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(root, 'docker', 'server.py')).read()
    line = [l for l in src.splitlines() if l.strip().startswith('for k in (')][0]
    assert "'time_limit_s'" in line               # config POST whitelist
    os.environ['AIR_DATA_DIR'] = tempfile.mkdtemp()
    loader = importlib.machinery.SourceFileLoader('air_server_61', os.path.join(root, 'docker', 'server.py'))
    spec = importlib.util.spec_from_loader('air_server_61', loader)
    srv = importlib.util.module_from_spec(spec); loader.exec_module(srv)
    assert srv.default_config()['routing']['time_limit_s'] == 0


def _variant_gate(unc, splits):
    return {'unconnected_total': unc, 'copper_violations': 0,
            'banned_layer_tracks': 0, 'split_nets': list(splits),
            'split_signal_nets': list(splits), 'split_pour_nets': [],
            'classes': {}}


def _variant_harness(state):
    import orchestrator as orc
    built, ran = [], []

    class _FakePipe:
        def __init__(self, cfg, board_path, log):
            built.append(cfg.get('no_tracks_on'))
            self.board_path = board_path
        def deadline_passed(self):
            return state['pipe_hit']

    class _Caller:
        board_path = 'board.kicad_pcb'
        def tool_board_summary(self):
            return {'copper_layers': ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'],
                    'pour_layers': {'GND': ['In1.Cu', 'In2.Cu']},
                    'layer_aliases': {}}

    def fake_once(cfg, pipe, log):
        ran.append(cfg.get('no_tracks_on'))
        if cfg.get('no_tracks_on') == 'In1.Cu,In2.Cu':
            return {'gate': _variant_gate(state['b_unc'], ['SIG1']),
                    'deadline_hit': state['res_hit'],
                    'final_board': 'B'}
        return {'gate': _variant_gate(state['a_unc'], []),
                'deadline_hit': state['a_hit'], 'final_board': 'A'}

    saved = (orc.Pipeline, orc._run_auto_once)
    orc.Pipeline, orc._run_auto_once = _FakePipe, fake_once
    try:
        res = orc.run_auto({'layer_law_policy': 'auto'}, _Caller(),
                           lambda m: None)
    finally:
        orc.Pipeline, orc._run_auto_once = saved
    return res, built, ran


def test_run_auto_no_escalation_after_deadline():
    # #241: Variant B deadline-bound -- by its result dict OR
    # by the pipe's own deadline flag -- with splits that
    # would otherwise escalate, must return B at once: no
    # second Pipeline is built and Variant A never runs.
    for state in ({'res_hit': True, 'pipe_hit': False},
                  {'res_hit': False, 'pipe_hit': True}):
        state.update({'b_unc': 3, 'a_unc': 0, 'a_hit': False})
        res, built, ran = _variant_harness(state)
        assert res['final_board'] == 'B', (state, res)
        assert built == ['In1.Cu,In2.Cu'], (state, built)
        assert ran == ['In1.Cu,In2.Cu'], (state, ran)


def test_run_auto_deadline_hit_ors_across_variants():
    # #241: escalation runs, A is deadline-bound but worse,
    # B is kept -- the kept result must still carry the run's
    # deadline label.
    state = {'res_hit': False, 'pipe_hit': False,
             'b_unc': 0, 'a_unc': 9, 'a_hit': True}
    res, built, ran = _variant_harness(state)
    assert res['final_board'] == 'B', res
    assert built == ['In1.Cu,In2.Cu', 'In1.Cu'], built
    assert res['deadline_hit'] is True, res


def test_portfolio_ledger_carries_deadline_detail():
    # #242: the first sequential leg resolves not-run/deadline
    # and the tail legs inherit the cause; every not-run entry
    # in the ledger record carries the detail (raced outcomes
    # lost theirs in this builder too, before the fix).
    with tempfile.TemporaryDirectory() as wd:
        pipe = _shell({'portfolio_workers': 1}, wd)
        pipe._check_deadline = lambda *a: None
        pipe._layer_cost_spec = lambda b: None
        pipe.deadline_passed = lambda: False
        records = []
        pipe._record = lambda kind, rec: records.append((kind, rec))
        pipe._run_leg = lambda eng: {'engine': eng,
                                     'status': 'not-run',
                                     'detail': 'deadline'}
        out = pipe.tool_portfolio(['tm_relax', 'freerouting', 'krt'])
        assert out is None
        rec = [r for k, r in records if k == 'portfolio'][0]
        by_eng = {e['engine']: e for e in rec['results']}
        for eng in ('tm_relax', 'freerouting', 'krt'):
            assert by_eng[eng]['termination'] == 'not-run', by_eng
            assert by_eng[eng].get('detail') == 'deadline', by_eng


def test_race_heat_interrupted_vs_never_started():
    # #242: a leg killed in flight by the deadline is
    # 'interrupted'; a leg that never started is not -- the
    # two read identically (not-run/deadline) without the flag.
    with tempfile.TemporaryDirectory() as wd:
        pipe = _shell({}, wd)
        pipe.deadline_passed = lambda: True
        pipe._leg_watchdog_s = lambda: 30

        class _Proc:
            def poll(self):
                return None
        pipe._spawn_leg = lambda i, eng: {'proc': _Proc(), 'engine': eng}
        pipe._poll_leg = lambda h: None      # still running
        pipe._kill_leg = lambda h: None
        outcomes = pipe._race_heat(['tm_relax'], 0)
        assert outcomes[0]['status'] == 'not-run', outcomes
        assert outcomes[0]['detail'] == 'deadline', outcomes
        assert outcomes[0].get('interrupted') is True, outcomes
        outcomes = pipe._race_portfolio(['tm_relax', 'krt'], 2)
        assert all(o['status'] == 'not-run'
                   and o.get('detail') == 'deadline'
                   and not o.get('interrupted') for o in outcomes), outcomes


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
    print('best-so-far/deadline OK: %d tests' % len(fns))
