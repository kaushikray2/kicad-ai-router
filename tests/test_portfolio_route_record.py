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

"""Issue #177: last_route was assigned only in tool_route and
_route_variant never asked the engine for its route JSON, so a
TraceMaker portfolio win (with the main passes then skipped) left
the job's route record -- exit_code, stopped_by, route_open_only,
open_at_start -- all-None. The variant now captures --json and
tool_portfolio seeds last_route from the winning variant's record.
Run: python3 tests/test_portfolio_route_record.py"""
import os, stat, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl
import orchestrator as orc

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
GATE = {'unconnected_total': 0, 'copper_violations': 0,
        'banned_layer_tracks': 0, 'split_nets': [], 'classes': {}}

FAKE_TM = '''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
out = a[a.index('-o') + 1]
open(out, 'w').write('board')
if '--json' in a:
    json.dump({'exit_code': 3, 'stopped_by': 'work', 'route_open_only': True,
               'open_at_start': 2, 'seconds': 1.5}, open(a[a.index('--json') + 1], 'w'))
sys.exit(3)
'''


def _pipe():
    d = tempfile.mkdtemp()
    tm = os.path.join(d, 'faketm')
    open(tm, 'w').write(FAKE_TM); os.chmod(tm, stat.S_IRWXU)
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.board_path = os.path.join(d, 'input.kicad_pcb')
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    open(pipe.base, 'w').write('board')
    pipe.cfg = {'tracemaker_path': tm}
    pipe.last_gate = None; pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    def fake_worker(cmd, *args, **kw):
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._worker = fake_worker
    pipe.tool_gate = lambda board: dict(GATE)
    return pipe


def test_tm_variant_win_seeds_route_record():
    pipe = _pipe()
    g = pipe.tool_portfolio(['tm_relax'])
    assert g is not None
    lr = pipe.last_route
    assert lr is not None, 'portfolio win left last_route empty'
    assert lr['exit_code'] == 3 and lr['stopped_by'] == 'work', lr
    assert lr['route_open_only'] is True and lr['open_at_start'] == 2, lr
    assert lr.get('source') == 'portfolio:tm_relax', lr
    info = orc._route_info(pipe)
    assert info == {'exit_code': 3, 'stopped_by': 'work', 'route_open_only': True, 'open_at_start': 2}, info
    recs = [e for e in pipe.ledger if e['step'] == 'route']
    assert recs and recs[0]['data'].get('source') == 'portfolio:tm_relax', pipe.ledger


def test_freerouting_win_stays_recordless():
    pipe = _pipe()
    g = pipe.tool_portfolio(['freerouting'])
    assert g is not None
    assert pipe.last_route is None
    assert orc._route_info(pipe) == {'exit_code': None, 'stopped_by': None,
                                     'route_open_only': None, 'open_at_start': None}


if __name__ == '__main__':
    test_tm_variant_win_seeds_route_record()
    test_freerouting_win_stays_recordless()
    print('portfolio route record OK')
