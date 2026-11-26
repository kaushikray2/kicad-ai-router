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

"""P2.7a: tool_gate serves a cached verdict for identical board bytes
under identical run state -- and never for anything else.

The policies re-gate unchanged boards constantly (the base twice at
AUTO start, the portfolio winner after selection, the current board
at stage boundaries a stage already gated internally, the final
board again by finalize's callers). A gate is a pure measurement of
(board bytes, cfg, base-reference state, census state), so a repeat
of the same bytes under the same state must not pay for another
kicad-cli DRC; a mutated board, a changed reference, a flipped
census or a changed config must always measure fresh.
Run: python3 tests/test_gate_verdict_cache.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
SUMM = {'nets': [], 'tracks_by_layer': {'In1.Cu': 4},
        'tracks_by_layer_net': {}, 'pour_layers': {}, 'pour_nets': [],
        'layer_aliases': dict(ALIASES), 'copper_layers': list(LAYERS),
        'vias': 0, 'wirelength_mm': 0.0}

def _bare_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)  # bypasses __init__ on purpose (see #101)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.board_path = pipe.base
    pipe.cfg = cfg; pipe.log = lambda *a: None
    pipe._copper_layers = list(LAYERS)
    pipe._gseq = 0; pipe.last_gate = None
    open(pipe.base, 'w').write('board')
    calls = {'drc': 0, 'summary': 0}
    def measure(board):
        calls['drc'] += 1
        return (5, 2, {'starved_thermal': 3}, 0, [])
    pipe._drc_measure = measure
    def worker(cmd, *a, **k):
        if cmd == 'summary': calls['summary'] += 1
        return dict(SUMM) if cmd == 'summary' else {}
    pipe._worker = worker
    return pipe, calls

def test_repeat_gate_of_identical_bytes_measures_once():
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    g1 = pipe.tool_gate()
    assert calls['drc'] == 1 and not g1.get('cached'), (calls, g1.get('cached'))
    assert g1['unconnected_total'] == 2 and g1['banned_layer_tracks'] == 4
    g2 = pipe.tool_gate()
    assert calls['drc'] == 1, calls  # served, not re-measured
    assert g2.get('cached') is True
    assert g2['unconnected_total'] == 2 and g2['banned_layer_tracks'] == 4
    assert g2['board'] == pipe.base and pipe.last_gate is g2
    # mutating a returned record must not poison the cached verdict
    g2['classes']['starved_thermal'] = 999
    g3 = pipe.tool_gate()
    assert calls['drc'] == 1 and g3['classes']['starved_thermal'] == 3

def test_mutated_board_is_regated():
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    pipe.tool_gate()
    assert calls['drc'] == 1
    open(pipe.base, 'w').write('board-mutated')  # same path, new bytes
    g = pipe.tool_gate()
    assert calls['drc'] == 2 and not g.get('cached'), (calls, g.get('cached'))
    g2 = pipe.tool_gate()
    assert calls['drc'] == 2 and g2.get('cached') is True

def test_same_bytes_other_path_served_with_its_own_board_field():
    # the finalize pattern: the delivered file can carry bytes a step
    # board was already gated under; the verdict is served, but the
    # record must name THIS path (callers compare last_gate['board'])
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    step = os.path.join(pipe.workdir, 'step-1.kicad_pcb')
    open(step, 'w').write('board')
    pipe.tool_gate(pipe.base)   # sets the base references
    pipe.tool_gate(step)        # #208: step byte-matches the base, so it is
    assert calls['drc'] == 1    # served the base's entry, not re-measured
    final = os.path.join(pipe.workdir, 'final.kicad_pcb')
    open(final, 'w').write('board')  # refill produced identical bytes
    g = pipe.tool_gate(final)
    assert calls['drc'] == 1 and g.get('cached') is True, calls
    assert g['board'] == final

def test_state_change_forces_fresh_measure():
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    step = os.path.join(pipe.workdir, 'step-1.kicad_pcb')
    open(step, 'w').write('board')
    pipe.tool_gate(step)            # references unset: deltas keyed vs itself
    assert calls['drc'] == 1
    pipe.tool_gate(pipe.base)       # the base gate sets the references
    assert calls['drc'] == 2
    g = pipe.tool_gate(step)        # #208: step byte-matches the base, so the base's
    assert calls['drc'] == 2, calls # entry serves it -- its deltas are the base's own
    assert g.get('cached') is True and g['board'] == step  # (0) under either reference state;
    pipe.tool_gate(step)            # a NON-identical board still re-measures here (below)
    assert calls['drc'] == 2
    pipe._locks_protected = True    # census reference flip (finalize pattern)
    pipe.tool_gate(step)
    assert calls['drc'] == 3, calls
    pipe._locks_protected = False
    pipe.cfg = {'no_tracks_on': 'In2.Cu'}  # config change: measure
    pipe.tool_gate(step)
    assert calls['drc'] == 4, calls


def test_sidecar_change_forces_fresh_measure():
    # #207: the DRC verdict is read against the sibling .kicad_pro
    # (netclasses) and .kicad_dru (custom rules) that _sidecars
    # places beside the board -- their bytes belong in the key.
    # The stubbed measurement plays kicad-cli: it derives its
    # verdict from the siblings beside the board it is handed.
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    def measure(board):
        calls['drc'] += 1
        stem = os.path.splitext(board)[0]
        pro = open(stem + '.kicad_pro').read() if os.path.exists(stem + '.kicad_pro') else ''
        dru = open(stem + '.kicad_dru').read() if os.path.exists(stem + '.kicad_dru') else ''
        total = 5 + (4 if 'WIDE' in pro else 0) + (2 if 'STRICT' in dru else 0)
        return (total, 2, {'starved_thermal': 3}, 0, [])
    pipe._drc_measure = measure
    pro = os.path.join(pipe.workdir, 'input.kicad_pro')
    open(pro, 'w').write('netclass A')
    g1 = pipe.tool_gate()
    assert g1['violations_total'] == 5 and calls['drc'] == 1, (g1, calls)
    g2 = pipe.tool_gate()
    assert g2.get('cached') is True and calls['drc'] == 1, (g2, calls)  # unchanged sidecars: served
    open(pro, 'w').write('netclass WIDE')  # ONLY the .kicad_pro changes; board bytes identical
    g3 = pipe.tool_gate()
    assert calls['drc'] == 2 and not g3.get('cached'), (calls, g3.get('cached'))
    assert g3['violations_total'] == 9, g3
    open(os.path.join(pipe.workdir, 'input.kicad_dru'), 'w').write('STRICT rules')
    g4 = pipe.tool_gate()
    assert calls['drc'] == 3 and not g4.get('cached'), (calls, g4.get('cached'))
    assert g4['violations_total'] == 11, g4


def test_byte_identical_file_shares_the_base_entry():
    # #208: a downstream file carrying the base's exact bytes (the
    # finalize pattern) must be served the base's verdict, not
    # re-measured -- its counts ARE the base's, so its deltas
    # against the base references are 0, exactly the base record's
    # self-relative deltas.
    pipe, calls = _bare_pipe({'no_tracks_on': 'In1.Cu'})
    pipe.tool_gate()                    # base measured; references set
    assert calls['drc'] == 1
    twin = os.path.join(pipe.workdir, 'twin.kicad_pcb')
    open(twin, 'w').write('board')      # same bytes, different path
    g = pipe.tool_gate(twin)
    assert calls['drc'] == 1 and g.get('cached') is True, calls
    assert g['board'] == twin

if __name__ == '__main__':
    test_repeat_gate_of_identical_bytes_measures_once()
    test_mutated_board_is_regated()
    test_same_bytes_other_path_served_with_its_own_board_field()
    test_state_change_forces_fresh_measure()
    test_sidecar_change_forces_fresh_measure()
    test_byte_identical_file_shares_the_base_entry()
    print('gate verdict cache OK')
