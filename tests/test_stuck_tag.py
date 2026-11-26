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

"""N6/#48: tool_route must tag the stuck report with the route OUTPUT
board (outp), not the pre-route board. Stubs the worker + TraceMaker."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

def test_last_stuck_board_is_output(tmp_path=None):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = {'tracemaker_path': '/bin/true', 'no_tracks_on': 'In1.Cu', 'work': 1000}
    pipe._copper_layers = ['F.Cu', 'In1.Cu', 'B.Cu']; pipe.ledger = []
    pipe.log = lambda *a: None
    def fake_worker(cmd, *args, **kw):
        if cmd == 'summary':
            return {'copper_layers': ['F.Cu', 'In1.Cu', 'B.Cu'],
                    'layer_aliases': {'f.cu': 'F.Cu', 'in1.cu': 'In1.Cu', 'b.cu': 'B.Cu'}}
        return {}
    pipe._worker = fake_worker
    real_run = pl.subprocess.run
    def fake_run(cmdline, stdout=None, **kw):
        outp = cmdline[cmdline.index('-o') + 1]
        open(outp, 'w').write('board')
        if stdout: stdout.write('routed 1/2 connections\n')
        class R: returncode = 0
        return R()
    pl.subprocess.run = fake_run
    try:
        pipe.tool_route()
    finally:
        pl.subprocess.run = real_run
    assert pipe.current != pipe.base
    assert pipe.last_stuck_board == pipe.current, (pipe.last_stuck_board, pipe.current)

if __name__ == '__main__':
    test_last_stuck_board_is_output(); print('stuck tag OK')
