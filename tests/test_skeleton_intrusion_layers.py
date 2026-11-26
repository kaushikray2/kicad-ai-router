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

"""Issue #198b: skeleton_intrusions tested every via against
every corridor layer-blind (a blind F->In1 via counted against
an In2 corridor it never touches, at F.Cu width) and treated
arcs as their chords (an arc bulging into a corridor was
missed). Vias now intrude only on layers they span; arcs are
sampled start->mid->end and count once.
Run: python3 tests/test_skeleton_intrusion_layers.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')
ld = importlib.machinery.SourceFileLoader('w198', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w198', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

NM = lambda v: int(v * 1e6)

def _board():
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    ng = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(ng)
    ns = pcbnew.NETINFO_ITEM(b, 'SIG'); b.Add(ns)
    return b, ng.GetNetCode(), ns.GetNetCode()

def _skel(layer_idx):
    return [{'net': 'GND', 'layer_idx': layer_idx, 'corridor': 1.0,
             'pads': [(10.0, 10.0), (30.0, 10.0)], 'edges': [(0, 1, 20.0)]}]

def _via(b, code, x, y, vtype=None, pair=None):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y)))
    v.SetWidth(NM(0.6)); v.SetDrill(NM(0.3)); v.SetNetCode(code)
    if vtype is not None: v.SetViaType(vtype)
    if pair is not None: v.SetLayerPair(pair[0], pair[1])
    b.Add(v)
    return v

def test_blind_via_does_not_intrude_on_untouched_layer():
    b, gc, sc = _board()
    _via(b, sc, 20, 10, pcbnew.VIATYPE_BLIND, (pcbnew.F_Cu, pcbnew.In1_Cu))  # on the In2 centreline, never on In2
    items, _mm, _pn = w.skeleton_intrusions(b, _skel(2))
    assert items == 0, items

def test_through_via_still_intrudes():
    b, gc, sc = _board()
    _via(b, sc, 20, 10)
    items, _mm, _pn = w.skeleton_intrusions(b, _skel(2))
    assert items == 1, items

def test_arc_bulge_into_corridor_counts_once():
    b, gc, sc = _board()
    a = pcbnew.PCB_ARC(b)
    a.SetStart(pcbnew.VECTOR2I(NM(14), NM(6))); a.SetMid(pcbnew.VECTOR2I(NM(20), NM(9.4))); a.SetEnd(pcbnew.VECTOR2I(NM(26), NM(6)))
    a.SetWidth(NM(0.25)); a.SetLayer(pcbnew.F_Cu); a.SetNetCode(sc); b.Add(a)
    items, _mm, _pn = w.skeleton_intrusions(b, _skel(0))  # chord sits 4 mm away; the bulge reaches 0.6 mm
    assert items == 1, items

if __name__ == '__main__':
    test_blind_via_does_not_intrude_on_untouched_layer()
    test_through_via_still_intrudes()
    test_arc_bulge_into_corridor_counts_once()
    print('skeleton intrusion layers OK')
