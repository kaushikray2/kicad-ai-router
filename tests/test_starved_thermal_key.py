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

"""Issue #116: starved_thermal was not a fault class, so neither
_accept_key nor _final_key could see it -- the NAS layer-cost runs
added starved thermals (12 -> 23) while every key improved or tied,
against the README's revert promise. The fix keys the
routing-caused DELTA (gate-baked, CP_delta style: base count
subtracted, floored at 0) as its own tier after the
fault/connectivity block and before the layer-cost tail, and
reports isolated_copper without keying it.
Run: python3 tests/test_starved_thermal_key.py"""
import importlib, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl
import orchestrator as orc

def gate(starved=0, faults=0, unc=5, splits=(), delta=None, classes_extra=None):
    classes = {'clearance': faults, 'starved_thermal': starved}
    if classes_extra: classes.update(classes_extra)
    g = {'classes': classes, 'unconnected_total': unc, 'split_nets': list(splits),
         'cost_weighted_wirelength_mm': None}
    if delta is not None: g['starved_thermal_delta'] = delta
    return g

# key level: at equal faults/connectivity/splits, added thermals lose
assert pl._accept_key(gate(delta=0)) < pl._accept_key(gate(delta=3))
assert orc._final_key(gate(delta=0)) < orc._final_key(gate(delta=3))
# the tier is subordinate: it never trades a connection or a fault
assert pl._accept_key(gate(unc=4, delta=9)) < pl._accept_key(gate(unc=5, delta=0))
assert pl._accept_key(gate(faults=0, delta=9)) < pl._accept_key(gate(faults=1, delta=0))
# ... and it still outranks the cost tail
assert pl._accept_key(gate(delta=0)) < pl._accept_key(dict(gate(delta=1), cost_weighted_wirelength_mm=0.0))
# legacy records without the baked field compare as delta 0
assert pl.starved_delta(gate()) == 0 and pl.starved_delta(None) == 0
assert pl._accept_key(gate()) == pl._accept_key(gate(delta=0))

# gate level: the delta is baked against the base board's own count
pipe = object.__new__(pl.Pipeline)
pipe.cfg = {'kicad_cli': 'kicad-cli', 'layer_cost': '', 'no_tracks_on': 'In1.Cu'}
pipe.workdir = tempfile.mkdtemp()
pipe.board_path = os.path.join(pipe.workdir, 'input.kicad_pcb')
pipe.base = os.path.join(pipe.workdir, 'base.kicad_pcb')
pipe.current = pipe.base
pipe._gseq = 0; pipe.ledger = []; pipe.last_gate = None
pipe.log = lambda m: None
SUMM = {'tracks_by_layer': {}, 'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
        'nets': [], 'vias': 0, 'wirelength_mm': 0.0, 'track_length_by_layer': {},
        'locked_items': 0, 'free_vias': 0, 'copper_layers': ['F.Cu', 'B.Cu'],
        'layer_aliases': {'f.cu': 'F.Cu', 'b.cu': 'B.Cu'}}
for _f in ('base.kicad_pcb', 'step1.kicad_pcb', 'step2.kicad_pcb'):
    open(os.path.join(pipe.workdir, _f), 'w').write('(kicad_pcb)')
pipe._worker = lambda cmd, *a, **k: dict(SUMM)
queue = []
pipe._drc_measure = lambda board: queue.pop(0)

def measure(starved, iso=0):
    classes = {'starved_thermal': starved, 'isolated_copper': iso}
    return (starved + iso, 5, classes, 0, [])

queue.append(measure(12, 2))            # base board: 12 pre-existing thermals
g_base = pipe.tool_gate(pipe.base)
assert g_base['starved_thermal'] == 12 and g_base['starved_thermal_delta'] == 0, g_base
assert g_base['isolated_copper'] == 2
queue.append(measure(12, 7))            # a step that merely inherits them
g_inherit = pipe.tool_gate(os.path.join(pipe.workdir, 'step1.kicad_pcb'))
assert g_inherit['starved_thermal_delta'] == 0, g_inherit
queue.append(measure(15, 2))            # a step that ADDS 3 thermals
g_add = pipe.tool_gate(os.path.join(pipe.workdir, 'step2.kicad_pcb'))
assert g_add['starved_thermal_delta'] == 3, g_add
assert pl._accept_key(g_inherit) < pl._accept_key(g_add), 'a thermal-adding step must lose at equal gate numbers'
print('PASS test_starved_thermal_key')
