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

"""Issue #174: Finish this board (route_open_only) promised "never
touches existing copper", but only the strip stage was held back:

- the portfolio still ran freerouting/krt (they re-route the base
  from scratch) when a user configured them;
- prune still deleted unlocked pad-less copper -- an unfinished
  user stub is exactly that;
- finish_leg / ilp_repair victim rips ignored lock flags entirely.

The fix: portfolio exclusion + prune skip under route_open_only,
and structural protection -- Pipeline.__init__ snapshots the
original lock flags and locks every pre-existing track/via on the
working base (worker lock_all), the victim rips skip locked items,
and finalize() restores the original locks on the delivered board.
Parts A/B are pure-unit; part C needs pcbnew + kicad-cli.
Run: python3 tests/test_route_open_only_protection.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
# Phase 2E (#66): the stub legs in this file run in-process
# (portfolio_workers=1; the parallel child path is covered by
# test_portfolio_racing.py), and the stub gate must not be
# COMPLETE -- under #66's first-complete selection an
# all-complete first leg ends the portfolio by design. These
# tests exercise the exclusion plumbing, so every surviving
# leg must run: the stub board stays unconnected.
GATE = {'unconnected_total': 2, 'copper_violations': 0,
        'banned_layer_tracks': 0, 'split_nets': [], 'classes': {}}


def _fake_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = dict(cfg); pipe.cfg.setdefault('portfolio_workers', 1); pipe.last_gate = dict(GATE); pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    pipe.calls = []
    def fake_worker(cmd, *args, **kw):
        pipe.calls.append(cmd)
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._worker = fake_worker
    def fake_variant(eng, outp):
        pipe.calls.append('variant:' + eng)
        open(outp, 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._route_variant = fake_variant
    pipe.tool_gate = lambda board: dict(GATE)
    return pipe


def test_portfolio_excludes_scratch_engines_when_route_open_only():
    pipe = _fake_pipe({'tracemaker_path': '/tm/tracemaker', 'route_open_only': True})
    pipe.tool_portfolio(['freerouting', 'krt', 'tm_relax'])
    assert 'freeroute' not in pipe.calls, pipe.calls
    assert 'krt_route' not in pipe.calls, pipe.calls
    assert pipe.calls.count('variant:tm_relax') == 1, pipe.calls
    rec = [e for e in pipe.ledger if e['step'] == 'portfolio'][0]['data']
    assert rec['engines'] == ['tm_relax'], rec
    assert rec.get('excluded_route_open_only') == ['freerouting', 'krt'], rec


def test_portfolio_unchanged_without_route_open_only():
    pipe = _fake_pipe({'tracemaker_path': '/tm/tracemaker'})
    pipe.tool_portfolio(['freerouting', 'krt', 'tm_relax'])
    assert 'freeroute' in pipe.calls and 'krt_route' in pipe.calls, pipe.calls


def test_prune_skipped_when_route_open_only():
    pipe = _fake_pipe({'route_open_only': True})
    r = pipe.tool_prune()
    assert r.get('status') == 'skipped_route_open_only', r
    assert 'prune' not in pipe.calls, pipe.calls


def test_prune_runs_otherwise():
    pipe = _fake_pipe({})
    pipe.tool_prune()
    assert 'prune' in pipe.calls, pipe.calls


def _pcbnew_part():
    import importlib.machinery, importlib.util
    try:
        import pcbnew
    except ImportError:
        import pytest
        pcbnew = pytest.importorskip('pcbnew')
    ld = importlib.machinery.SourceFileLoader('w_uut', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
    sp = importlib.util.spec_from_loader('w_uut', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

    def mm(v): return int(v * 1e6)
    def track(b, code, x1, y1, x2, y2, locked=False):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(mm(x1), mm(y1))); t.SetEnd(pcbnew.VECTOR2I(mm(x2), mm(y2)))
        t.SetWidth(mm(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(code)
        t.SetLocked(locked); b.Add(t); return t
    def locks(path):
        b = pcbnew.LoadBoard(path)
        return {t.m_Uuid.AsString(): bool(t.IsLocked()) for t in b.GetTracks()}

    d = tempfile.mkdtemp()
    b = pcbnew.BOARD()
    net = pcbnew.NETINFO_ITEM(b, 'SIG'); b.Add(net); code = net.GetNetCode()
    ta = track(b, code, 10, 10, 20, 10)                 # unfinished user stub, unlocked
    tb = track(b, code, 30, 30, 40, 30)                 # another pad-less segment, unlocked
    tc = track(b, code, 50, 50, 60, 50, locked=True)    # user-locked routing
    orig = {t.m_Uuid.AsString(): bool(t.IsLocked()) for t in b.GetTracks()}
    src = os.path.join(d, 'board.kicad_pcb'); pcbnew.SaveBoard(src, b)

    # control: the stub IS prunable on the unprotected board
    r = w.cmd_prune(src, os.path.join(d, 'pruned.kicad_pcb'))
    assert r.get('pruned') == 2, r
    assert len(locks(os.path.join(d, 'pruned.kicad_pcb'))) == 1

    # protected run: __init__ locks everything pre-existing
    logs = []
    cfg = {'route_open_only': True, 'strip_existing': True, 'tracemaker_path': '/nonexistent',
           'kicad_cli': 'kicad-cli', 'layer_cost': '', 'no_tracks_on': 'In1.Cu'}
    pipe = pl.Pipeline(cfg, src, logs.append)
    assert pipe.cfg['strip_existing'] is False  # coerced on the run's own copy (#189; the #174-era assert read the caller's dict, which is exactly the aliasing #189 removes)
    # #189: that coercion must live on the Pipeline's OWN copy --
    # a second construction from a fresh caller dict proves the
    # caller's dict is not the thing being written to.
    cfg2 = {'route_open_only': True, 'strip_existing': True, 'tracemaker_path': '/nonexistent',
            'kicad_cli': 'kicad-cli', 'layer_cost': '', 'no_tracks_on': 'In1.Cu'}
    pipe2 = pl.Pipeline(cfg2, src, logs.append)
    assert cfg2['strip_existing'] is True, 'Pipeline.__init__ mutated the caller cfg (#189)'
    assert pipe2.cfg['strip_existing'] is False
    base_locks = locks(pipe.base)
    assert len(base_locks) == 3 and all(base_locks.values()), base_locks
    # the gate censuses invariance against the PROTECTED base here
    g0 = pipe.tool_gate(pipe.base)
    assert g0['invariance']['input_locked_items'] == 3, g0['invariance']
    assert g0['invariance']['locked_items'] == 3 and g0['invariance']['ok'], g0['invariance']
    # prune is skipped and the working board is untouched
    r = pipe.tool_prune()
    assert r.get('status') == 'skipped_route_open_only', r
    assert locks(pipe.current) == base_locks
    # finalize restores the ORIGINAL lock flags on the delivered board
    final = pipe.finalize()
    fin = locks(final)
    assert fin == orig, (fin, orig)
    g1 = pipe.tool_gate(final)
    assert g1['invariance']['input_locked_items'] == 1, g1['invariance']
    assert g1['invariance']['locked_items'] == 1 and g1['invariance']['ok'], g1['invariance']


def test_pcbnew_protection_roundtrip():
    _pcbnew_part()


if __name__ == '__main__':
    test_portfolio_excludes_scratch_engines_when_route_open_only()
    test_portfolio_unchanged_without_route_open_only()
    test_prune_skipped_when_route_open_only()
    test_prune_runs_otherwise()
    _pcbnew_part()
    print('route_open_only protection OK')
