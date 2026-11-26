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

"""v1.11.2/#101: tool_gate must not pay for two `summary --fast`
worker runs per board -- it fetches one summary for its census and
_effective_policy -> _layer_info used to fetch a second one for the
same (fresh step) board. The gate now seeds the layer-info cache
with the summary it already holds.
Run: python3 tests/test_gate_summary_once.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
SUMM = {'nets': [], 'tracks_by_layer': {'In1.Cu': 3, 'In2.Cu': 5},
        'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
        'layer_aliases': dict(ALIASES), 'copper_layers': list(LAYERS),
        'vias': 0, 'wirelength_mm': 0.0}

def _bare_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)  # bypasses __init__ on purpose (see #101)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(LAYERS)
    pipe._gseq = 0; pipe.last_gate = None
    open(pipe.base, 'w').write('board')
    pipe._drc_measure = lambda board: (0, 0, {}, 0, [])
    calls = []
    def counting_worker(cmd, *a, **k):
        calls.append(cmd)
        return dict(SUMM) if cmd == 'summary' else {}
    pipe._worker = counting_worker
    return pipe, calls

def test_gate_summarises_once_per_board():
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu,In2.Cu', 'layer_cost': 'In2.Cu=10'})
    g = pipe.tool_gate()
    assert calls.count('summary') == 1, calls  # was 2 before #101
    # the seeded cache still drives the census: In2 is priced, so only
    # In1's 3 tracks count as banned-layer tracks
    assert g['banned_layer_tracks'] == 3, g['banned_layer_tracks']
    g2 = pipe.tool_gate()
    # the gate's own census fetch is per-gate by design (it measures
    # the board as it stands now); the invariant is ONE summary per
    # gate, not two -- the layer-info cache serves the policy half.
    assert calls.count('summary') == 2, calls
    assert g2['banned_layer_tracks'] == 3

if __name__ == '__main__':
    test_gate_summarises_once_per_board()
    print('gate summary once OK')
