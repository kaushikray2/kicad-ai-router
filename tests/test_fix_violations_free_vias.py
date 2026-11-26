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

"""Issue #195: (a) _fix_one_violation ripped free (stitching)
vias of the fault net -- #187 preserved their flag on rollback,
but a SUCCESSFUL repair deleted them outright, tripping the #119
invariance; (b) _zone_layers_by_net missed #181's rule-area skip,
so a rule area retaining a net name made _via_is_free call
ordinary routing vias free; (c) the rollback 'drawn' keys were
hand-built tuples duplicating add_seg/add_via's results instead
of _track_key of the created item.
Run: python3 tests/test_fix_violations_free_vias.py  (needs pcbnew)"""
import importlib.machinery, importlib.util, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
except ImportError:
    import pytest
    pcbnew = pytest.importorskip('pcbnew')
ld = importlib.machinery.SourceFileLoader('w195', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'worker.py'))
sp = importlib.util.spec_from_loader('w195', ld); w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)

NM = lambda v: int(v * 1e6)
_refseq = [0]

def mk_pad(b, code, x, y):
    _refseq[0] += 1
    fp = pcbnew.FOOTPRINT(b); fp.SetReference('U%d' % _refseq[0])
    p = pcbnew.PAD(fp)
    p.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y))); p.SetSize(pcbnew.VECTOR2I(NM(1.0), NM(1.0)))
    p.SetShape(pcbnew.PAD_SHAPE_RECT); p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); p.SetLayerSet(ls)
    p.SetNumber('1'); p.SetNetCode(code); fp.Add(p); b.Add(fp)
    return p

def mk_track(b, code, x1, y1, x2, y2, layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(NM(x1), NM(y1))); t.SetEnd(pcbnew.VECTOR2I(NM(x2), NM(y2)))
    t.SetWidth(NM(0.25)); t.SetLayer(layer); t.SetNetCode(code); b.Add(t)
    return t

def mk_via(b, code, x, y, free=False):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y)))
    v.SetWidth(NM(0.6)); v.SetDrill(NM(0.3)); v.SetNetCode(code)
    if free: v.SetIsFree(True)
    b.Add(v)
    return v

def base_board():
    b = pcbnew.BOARD()
    na = pcbnew.NETINFO_ITEM(b, 'A'); b.Add(na); ac = na.GetNetCode()
    mk_pad(b, ac, 20, 20); mk_pad(b, ac, 60, 20)
    mk_track(b, ac, 20, 20, 60, 20)
    return b, ac

def vias_of(b):
    return [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]

def test_free_via_survives_successful_repair():
    b, ac = base_board()
    mk_track(b, ac, 40, 20, 40, 26)          # dangling stub: the fault
    fv = mk_via(b, ac, 41, 24, free=True)    # stitching via inside the rip radius
    mk_via(b, ac, 39, 24)                    # ordinary via, also inside
    assert w._fix_one_violation(b, 'A', 40, 26) is True
    survivors = vias_of(b)
    assert len(survivors) == 1 and bool(survivors[0].GetIsFree()), [(s.GetPosition().x, bool(s.GetIsFree())) for s in survivors]
    conn = b.GetConnectivity(); conn.Build(b)
    assert len(w._pad_clusters(b, conn, 'A')[1]) == 1

def test_rule_area_does_not_make_vias_free():
    b = pcbnew.BOARD()
    na = pcbnew.NETINFO_ITEM(b, 'A'); b.Add(na); ac = na.GetNetCode()
    mk_pad(b, ac, 20, 20); mk_pad(b, ac, 60, 20)
    mk_track(b, ac, 20, 20, 60, 20)
    ka = pcbnew.ZONE(b)
    ka.SetIsRuleArea(True); ka.SetDoNotAllowTracks(True)
    ka.SetNet(na)  # a rule area RETAINING its net name in the file is the #181 condition
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ka.SetLayerSet(ls)
    o = ka.Outline(); o.NewOutline(); ch = o.Outline(0)
    for (x, y) in [(15, 5), (65, 5), (65, 35), (15, 35)]: ch.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
    ch.SetClosed(True); b.Add(ka)
    v = mk_via(b, ac, 30, 20)                # ordinary routing via, flag False
    assert w._via_is_free(v, w._zone_layers_by_net(b)) is False
    # control: a real pour zone on the via's layer still frees it (the #119 fallback)
    pz = pcbnew.ZONE(b); pz.SetNet(na)
    ls2 = pcbnew.LSET(); ls2.AddLayer(pcbnew.F_Cu); pz.SetLayerSet(ls2)
    o2 = pz.Outline(); o2.NewOutline(); ch2 = o2.Outline(0)
    for (x, y) in [(15, 5), (65, 5), (65, 35), (15, 35)]: ch2.Append(pcbnew.VECTOR2I(NM(x), NM(y)))
    ch2.SetClosed(True); b.Add(pz)
    assert w._via_is_free(v, w._zone_layers_by_net(b)) is True

def test_drawn_keys_come_from_created_items():
    b, ac = base_board()
    nets = b.GetNetsByName()
    t = w.add_seg(b, nets, 'A', 0, 10, 10, 12, 10, 0.25)
    assert t is not None and w._track_key(t)[0] == 'seg'
    v = w.add_via(b, nets, 'A', 10, 10, 0.6, 0.3)
    assert v is not None
    k = w._track_key(v)
    assert k == ('via', ac, NM(10), NM(10), NM(0.6), NM(0.3), pcbnew.VIATYPE_THROUGH, pcbnew.F_Cu, pcbnew.B_Cu, False), k

if __name__ == '__main__':
    test_free_via_survives_successful_repair()
    test_rule_area_does_not_make_vias_free()
    test_drawn_keys_come_from_created_items()
    print('fix_violations free vias OK')
