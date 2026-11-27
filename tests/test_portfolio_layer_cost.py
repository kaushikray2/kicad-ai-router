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

"""#109: while a valid layer_cost spec is in force, the portfolio
excludes the engines that cannot honour the price (freerouting,
krt) -- their priced-layer tracks carry no gate penalty, so they
would compete at 1x against TraceMaker's priced cost invisibly.
With layer_cost off (or invalid, hence off), the candidate set is
exactly what it was before the fix.
Run: python3 tests/test_portfolio_layer_cost.py"""
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


def _pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = dict(cfg); pipe.cfg.setdefault('portfolio_workers', 1); pipe.last_gate = dict(GATE); pipe.last_route = None
    pipe.logs = []
    pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    pipe.calls = []
    def fake_worker(cmd, *args, **kw):
        pipe.calls.append(cmd)
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS),
                    'layer_aliases': dict(ALIASES)}
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


def _portfolio_rec(pipe):
    recs = [e for e in pipe.ledger if e['step'] == 'portfolio']
    assert len(recs) == 1, pipe.ledger
    return recs[0]['data']


def test_non_honouring_engines_excluded_under_layer_cost():
    pipe = _pipe({'tracemaker_path': '/tm/tracemaker',
                  'layer_cost': 'In1.Cu=10,In2.Cu=10'})
    pipe.tool_portfolio(['freerouting', 'krt', 'tm_relax'])
    assert 'freeroute' not in pipe.calls, pipe.calls
    assert 'krt_route' not in pipe.calls, pipe.calls
    assert pipe.calls.count('variant:tm_relax') == 1, pipe.calls
    rec = _portfolio_rec(pipe)
    assert rec['engines'] == ['tm_relax'], rec
    assert rec['excluded_layer_cost'] == ['freerouting', 'krt'], rec
    assert [r['engine'] for r in rec['results']] == ['tm_relax'], rec
    excl = [m for m in pipe.logs if 'excluded' in m]
    assert len(excl) == 2 and any('freerouting' in m for m in excl) \
        and any('krt' in m for m in excl), pipe.logs


def test_portfolio_unchanged_without_layer_cost():
    pipe = _pipe({'tracemaker_path': '/tm/tracemaker'})
    pipe.tool_portfolio(['freerouting', 'krt', 'tm_relax'])
    assert 'freeroute' in pipe.calls and 'krt_route' in pipe.calls, pipe.calls
    assert pipe.calls.count('variant:tm_relax') == 1, pipe.calls
    rec = _portfolio_rec(pipe)
    assert rec['engines'] == ['freerouting', 'krt', 'tm_relax'], rec
    assert 'excluded_layer_cost' not in rec, rec
    assert not [m for m in pipe.logs if 'excluded' in m], pipe.logs


def test_invalid_layer_cost_is_off_and_excludes_nothing():
    # an invalid spec is treated as off (#73), so #109 must not fire
    pipe = _pipe({'tracemaker_path': '/tm/tracemaker',
                  'layer_cost': 'In1.Cu=0'})
    pipe.tool_portfolio(['freerouting', 'krt'])
    assert 'freeroute' in pipe.calls and 'krt_route' in pipe.calls, pipe.calls
    rec = _portfolio_rec(pipe)
    assert 'excluded_layer_cost' not in rec, rec


if __name__ == '__main__':
    test_non_honouring_engines_excluded_under_layer_cost()
    test_portfolio_unchanged_without_layer_cost()
    test_invalid_layer_cost_is_off_and_excludes_nothing()
    print('portfolio layer_cost exclusion OK')
