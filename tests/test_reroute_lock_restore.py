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

"""Issue #124: when restore_locks failed after a windowed re-route,
tool_reroute_window logged a WARNING and carried on to gate/accept
-- so a board whose user locks were never restored could be
accepted and delivered (its items unprotected against later
strip/prune, #119). A failed restore is now a failed attempt: the
re-route is discarded and the pre-reroute board stays current.
The worker and the engine subprocess are stubbed; the gate is
stubbed to IMPROVE, so on the old code the run is accepted --
that acceptance is the bug.
Run: python3 tests/test_reroute_lock_restore.py"""
import os, subprocess as _sp, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

GATE_BEFORE = {'classes': {}, 'unconnected_total': 5, 'split_nets': [], 'starved_thermal_delta': 0}
GATE_AFTER = {'classes': {}, 'unconnected_total': 0, 'split_nets': [], 'starved_thermal_delta': 0}

def make_pipeline(workdir):
    p = object.__new__(pl.Pipeline)
    p.workdir = workdir; p._oseq = 0
    p.cfg = {'tracemaker_path': '/bin/true', 'work': 1}
    p.current = os.path.join(workdir, 'in.kicad_pcb')
    open(p.current, 'w').write('input board')
    p.last_gate = dict(GATE_BEFORE)
    p.records = []
    p.log = lambda *a: None
    p._record = lambda kind, rec: p.records.append((kind, rec))
    p._sidecars = lambda b: None
    p._banned_for = lambda board: []
    p._layer_cost_spec = lambda board: None
    p.tool_gate = lambda board=None: dict(GATE_AFTER)
    def fake_worker(cmd, *args):
        if cmd == 'lock_window':
            out = args[args.index('--out') + 1]; open(out, 'w').write('locked board')
            return {'status': 'locked', 'locks_file': os.path.join(workdir, 'locks.json')}
        if cmd == 'restore_locks': raise RuntimeError('restore boom')
        if cmd == 'summary': return {'copper_layers': ['F.Cu', 'B.Cu']}
        return {}
    p._worker = fake_worker
    return p

def fake_engine(cmdline, **kw):
    open(cmdline[cmdline.index('-o') + 1], 'w').write('engine out')
    class R: returncode = 0
    return R()

wd = tempfile.mkdtemp()
p = make_pipeline(wd)
orig_run = _sp.run; _sp.run = fake_engine
try:
    res = p.tool_reroute_window('SIG')
finally:
    _sp.run = orig_run
assert res.get('status') == 'lock_restore_failed', 'failed lock restore was not treated as a failed attempt: %r' % (res,)
assert p.current.endswith('in.kicad_pcb'), 're-route with unrestored locks was ACCEPTED (current=%s)' % p.current
assert any(r.get('status') == 'lock_restore_failed' for _k, r in p.records), 'no lock_restore_failed record: %r' % (p.records,)
print('PASS test_reroute_lock_restore')
