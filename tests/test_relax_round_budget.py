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

"""Issue #52: the default portfolio candidate (tm_relax) was
bounded by wall-clock time, so the default path was not
reproducible. The engine budgets a relax run by rounds, not by
--work (its own help: '--work does not limit it, --time does'),
and the shipped default line was --work 40000000 --time 1800:
on any board where relax did not finish inside 30 minutes the
candidate was whatever the clock allowed -- a function of
machine speed and load.

Fix pinned here: route_flags bounds a relax leg by explicit
--round-work/--rounds in EVERY mode (the #184 translation,
with the item-3 sizing correction: the budget is floored at
50 rounds -- the configured work number never bounded relax,
and the probes showed a 2-round budget binds and degrades
the candidate, while 50 saturates every bench board) and
keeps --time as the backstop limit in default mode (with
--time absent the engine caps the tournament itself at
~120 s, which is worse and still a clock). The leg's route
record carries the round budget, and the portfolio ledger
record carries rounds + seconds + time_limited per finished
leg, so a run proves from its own ledger which bound ended
each leg -- at the floored budget, neither: the natural stop.
Run: python3 tests/test_relax_round_budget.py"""
import json, os, stat, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import engine_run as er
import pipeline as pl


def test_round_budget_translation():
    assert er.relax_round_budget(40000000) == (20000000, 50)
    assert er.relax_round_budget(20000000) == (20000000, 50)
    assert er.relax_round_budget(50000000) == (20000000, 50)
    assert er.relax_round_budget(1) == (20000000, 50)
    assert er.relax_round_budget(10 ** 10) == (20000000, 200)
    assert er.relax_round_budget(0) is None
    assert er.relax_round_budget(-5) is None
    assert er.relax_round_budget('junk') is None
    assert er.relax_round_budget(None) is None


def test_route_flags_relax_default_mode():
    # the shipped default leg: the floored rounds budget, with
    # --time kept as the backstop limit it finishes well inside
    flags = er.route_flags({}, 40000000, 1800, relax=True)
    assert flags == ['--work', '40000000', '--round-work', '20000000',
                     '--rounds', '50', '--time', '1800'], flags
    # route_open_only still composes ahead of the budget
    flags = er.route_flags({'route_open_only': True}, 40000000, 1800, relax=True)
    assert flags == ['--work', '40000000', '--route-open-only',
                     '--round-work', '20000000', '--rounds', '50', '--time', '1800'], flags
    # an unusable budget keeps the historical line (loud via the
    # engine's own behaviour, never a silent unbounded relax)
    assert er.route_flags({}, 0, 1800, relax=True) == ['--work', '0', '--time', '1800']
    # non-relax lines are byte-identical to before #52
    assert er.route_flags({}, 40000000, 1800) == ['--work', '40000000', '--time', '1800']
    assert er.route_flags({'deterministic': True}, 40000000, relax=True) == \
        ['--work', '40000000', '--round-work', '20000000', '--rounds', '50', '--deterministic']


# ------------------------------------------------- leg + ledger record
ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
_GATE = {'unconnected_total': 3, 'copper_violations': 1,
         'banned_layer_tracks': 0, 'split_nets': [],
         'classes': {'clearance': 1}}
_FP = {'footprints': [['U1', 0, 0, 0.0, False]], 'pads': [], 'zones': [], 'edges': []}


def _pipe():
    # The _pipe pattern of test_relax_deterministic.py, extended:
    # the fake engine's route JSON carries time_limited, the fake
    # worker answers the design-fingerprint op, the gate is stubbed.
    d = tempfile.mkdtemp()
    rec = os.path.join(d, 'argv.json')
    tm = os.path.join(d, 'faketm')
    body = ('#!/usr/bin/env python3\n'
            'import json, sys\n'
            'a = sys.argv[1:]\n'
            'json.dump(a, open(%r, "w"))\n'
            'out = a[a.index("-o") + 1]\n'
            'open(out, "w").write("board")\n'
            'if "--json" in a:\n'
            '    json.dump({"exit_code": 3, "seconds": 12.5, "time_limited": False}, open(a[a.index("--json") + 1], "w"))\n'
            'sys.exit(3)\n') % rec
    open(tm, 'w').write(body); os.chmod(tm, stat.S_IRWXU)
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.board_path = os.path.join(d, 'input.kicad_pcb')
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('board')
    pipe.cfg = {'tracemaker_path': tm, 'work': 40000000}
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    pipe._layer_cost_spec = lambda board: ''
    pipe.tool_gate = lambda board: dict(_GATE)
    def fake_worker(cmd, *args, **kw):
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if cmd == 'design_fingerprint':
            return dict(_FP)
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._worker = fake_worker
    return pipe, rec


def test_leg_record_carries_rounds_and_time_limited():
    pipe, rec = _pipe()
    oc = pipe._run_leg('tm_relax')
    assert oc['status'] == 'finished', oc
    rt = oc['route']
    assert rt['rounds'] == {'round_work': 20000000, 'rounds': 50}, rt
    assert rt['time_limited'] is False, rt
    assert oc['seconds'] == 12.5, oc
    argv = json.load(open(rec))
    assert '--rounds' in argv and argv[argv.index('--time') + 1] == '1800', argv


def test_portfolio_record_carries_leg_run_facts():
    pipe, _rec = _pipe()
    g = pipe.tool_portfolio(['tm_relax'])
    assert g is not None
    recs = [e for e in pipe.ledger if e['step'] == 'portfolio']
    assert len(recs) == 1, pipe.ledger
    (entry,) = recs[0]['data']['results']
    assert entry['engine'] == 'tm_relax' and entry['termination'] == 'finished'
    assert entry['rounds'] == {'round_work': 20000000, 'rounds': 50}, entry
    assert entry['time_limited'] is False, entry
    assert entry['seconds'] == 12.5, entry


if __name__ == '__main__':
    test_round_budget_translation()
    test_route_flags_relax_default_mode()
    test_leg_record_carries_rounds_and_time_limited()
    test_portfolio_record_carries_leg_run_facts()
    print('relax round budget OK')
