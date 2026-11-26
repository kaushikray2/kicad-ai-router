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

"""Issue #193: a FAILED engine run that still wrote an output
board must not move run state. tool_route assigned current /
last_route / last_stuck_board before the engine_error raise;
_route_variant consulted engine_error only when the output was
missing, so a failed variant competed in the portfolio;
tool_reroute_window discarded the return code and never read its
route JSON. A fake engine binary (exit code via $FAKE_TM_EXIT)
drives all three paths. Run:
python3 tests/test_failed_engine_state.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
from pipeline import Pipeline, PipelineError

FAKE = '''#!/usr/bin/env python3
import json, os, shutil, sys
args = sys.argv[1:]
code = int(os.environ.get('FAKE_TM_EXIT', '0'))
out = args[args.index('-o') + 1]
shutil.copyfile(args[args.index('route') + 1], out)
if '--json' in args:
    json.dump({'exit_code': code, 'routed': 0, 'connections': 0}, open(args[args.index('--json') + 1], 'w'))
print('fake engine message for exit %d' % code)
sys.exit(code)
'''

def _pipe():
    d = tempfile.mkdtemp()
    fake = os.path.join(d, 'fake-tm')
    with open(fake, 'w') as f: f.write(FAKE)
    os.chmod(fake, 0o755)
    board = os.path.join(d, 'b.kicad_pcb')
    with open(board, 'w') as f: f.write('(kicad_pcb)')
    logs = []
    pipe = Pipeline({'strip_existing': False, 'route_open_only': False,
                     'tracemaker_path': fake}, board, logs.append)
    pipe._worker = lambda *a, **k: {}
    pipe._sidecars = lambda p: None
    pipe._banned_for = lambda b: []
    pipe._layer_cost_spec = lambda b: ''
    return pipe

def test_tool_route_failure_moves_no_state():
    pipe = _pipe()
    os.environ['FAKE_TM_EXIT'] = '1'
    try:
        pipe.tool_route()
        raise AssertionError('tool_route should have raised')
    except PipelineError:
        pass
    assert pipe.current == pipe.base, pipe.current
    assert pipe.last_route is None, pipe.last_route
    assert getattr(pipe, 'last_stuck_board', None) is None
    # control: a clean pass still routes and moves state
    os.environ['FAKE_TM_EXIT'] = '0'
    rec = pipe.tool_route()
    assert pipe.current != pipe.base and pipe.last_route is rec

def test_route_variant_failure_is_a_failed_leg():
    pipe = _pipe()
    outp = os.path.join(pipe.workdir, 'pf-tm-relax.kicad_pcb')
    os.environ['FAKE_TM_EXIT'] = '1'
    try:
        pipe._route_variant('tm_relax', outp)
        raise AssertionError('_route_variant should have raised')
    except PipelineError:
        pass
    os.environ['FAKE_TM_EXIT'] = '0'
    r = pipe._route_variant('tm_relax', outp)
    assert r['status'] == 'routed' and r['route']['exit_code'] == 0

def test_reroute_window_failure_is_engine_failed():
    pipe = _pipe()
    gate = {'unconnected_total': 1, 'split_nets': [], 'split_signal_nets': [], 'split_pour_nets': []}
    pipe.last_gate = dict(gate)
    gated = []
    pipe.tool_gate = lambda b=None: (gated.append(b), dict(gate))[1]
    def worker(cmd, *a, **k):
        if cmd == 'lock_window':
            import shutil; shutil.copyfile(pipe.current, a[a.index('--out') + 1])
            return {'status': 'locked'}
        return {}
    pipe._worker = worker
    os.environ['FAKE_TM_EXIT'] = '1'
    r = pipe.tool_reroute_window('GND')
    assert r['status'] == 'engine_failed', r
    assert not gated, 'the failed board was gated for acceptance'

if __name__ == '__main__':
    test_tool_route_failure_moves_no_state()
    test_route_variant_failure_is_a_failed_leg()
    test_reroute_window_failure_is_engine_failed()
    print('failed engine state OK')
