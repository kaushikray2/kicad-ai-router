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

"""Issue #175: fault_count added the RAW banned_layer_tracks census,
so a partially routed board's inherited inner-layer tracks charged
any variant whose law banned their layer in full -- under
route_open_only + Automatic law, Variant B (own plane detection)
started with the inherited count as faults where Variant A (forced
In1.Cu-only) started at zero. The fix mirrors #116: the gate bakes
banned_layer_tracks_delta (candidate minus the base board's own
count under the same cfg, floored at 0) and fault_count keys on
the delta; records without the baked field fall back to the raw
count so old ledgers keep their meaning.
Run: python3 tests/test_banned_layer_delta.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

def gate(raw=None, delta=None, faults=0, unc=5):
    g = {'classes': {'clearance': faults}, 'unconnected_total': unc, 'split_nets': [],
         'cost_weighted_wirelength_mm': None}
    if raw is not None: g['banned_layer_tracks'] = raw
    if delta is not None: g['banned_layer_tracks_delta'] = delta
    return g

# key level: inherited tracks no longer charge a baked record
assert pl.banned_delta(gate(raw=7, delta=0)) == 0
assert pl.banned_delta(gate(raw=9, delta=2)) == 2
assert pl.fault_count(gate(raw=7, delta=0)) == pl.fault_count(gate(raw=0, delta=0))
assert pl.fault_count(gate(raw=9, delta=2)) == pl.fault_count(gate(raw=0, delta=0)) + 2
# the A/B asymmetry case: same board, B's law bans the inherited
# layer, A's does not -- equal work must compare equal
assert pl.fault_count(gate(raw=7, delta=0)) == pl.fault_count(gate(raw=0, delta=0))
assert pl._accept_key(gate(raw=7, delta=0)) == pl._accept_key(gate(raw=0, delta=0))
# legacy records (no baked delta) keep the raw-count meaning
assert pl.banned_delta(gate(raw=7)) == 7
assert pl.fault_count(gate(raw=7)) == pl.fault_count(gate(raw=0)) + 7
assert pl.banned_delta(None) == 0 and pl.banned_delta({}) == 0

# gate level: the delta is baked against the base board's own count
pipe = object.__new__(pl.Pipeline)
pipe.cfg = {'kicad_cli': 'kicad-cli', 'layer_cost': '', 'no_tracks_on': 'In1.Cu'}
pipe.workdir = tempfile.mkdtemp()
pipe.board_path = os.path.join(pipe.workdir, 'input.kicad_pcb')
pipe.base = os.path.join(pipe.workdir, 'base.kicad_pcb')
pipe.current = pipe.base
pipe._gseq = 0; pipe.ledger = []; pipe.last_gate = None
pipe.log = lambda m: None
for _f in ('base.kicad_pcb', 'step1.kicad_pcb', 'step2.kicad_pcb'):
    open(os.path.join(pipe.workdir, _f), 'w').write('(kicad_pcb)')
COUNTS = {'base.kicad_pcb': 7, 'step1.kicad_pcb': 7, 'step2.kicad_pcb': 9}
def summ_for(cmd, *a, **k):
    board = a[a.index('--board') + 1] if '--board' in a else ''
    n = COUNTS.get(os.path.basename(board), 0)
    return {'tracks_by_layer': {'In1.Cu': n} if n else {}, 'tracks_by_layer_net': {},
            'pour_layers': {}, 'pour_nets': [], 'nets': [], 'vias': 0,
            'wirelength_mm': 0.0, 'track_length_by_layer': {},
            'locked_items': 0, 'free_vias': 0,
            'copper_layers': ['F.Cu', 'In1.Cu', 'B.Cu'],
            'layer_aliases': {'f.cu': 'F.Cu', 'in1.cu': 'In1.Cu', 'b.cu': 'B.Cu'}}
pipe._worker = summ_for
pipe._drc_measure = lambda board: (0, 5, {}, 0, [])

g_base = pipe.tool_gate(pipe.base)
assert g_base['banned_layer_tracks'] == 7 and g_base['banned_layer_tracks_delta'] == 0, g_base
assert pl.fault_count(g_base) == 0, g_base
g_inherit = pipe.tool_gate(os.path.join(pipe.workdir, 'step1.kicad_pcb'))
assert g_inherit['banned_layer_tracks_delta'] == 0 and pl.fault_count(g_inherit) == 0, g_inherit
g_add = pipe.tool_gate(os.path.join(pipe.workdir, 'step2.kicad_pcb'))
assert g_add['banned_layer_tracks'] == 9 and g_add['banned_layer_tracks_delta'] == 2, g_add
assert pl.fault_count(g_add) == 2, g_add
print('PASS test_banned_layer_delta')
