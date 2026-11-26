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

"""N5/#47: a floating SMD pad over its own net's inner plane must NOT
be rejected by _bridge_island's 8 mm pre-filter (the old code measured
distance to the plane's outer ring only; the pad sits 18 mm from it).
Requires pcbnew (KiCad python); skipped elsewhere."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
    import worker as w
except Exception:
    pcbnew = None

def nm(x): return int(round(x * 1e6))

def test_pad_over_own_plane_not_filtered():
    if pcbnew is None:
        print('SKIP: no pcbnew'); return
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    net = pcbnew.NETINFO_ITEM(b, 'GND'); b.Add(net); code = net.GetNetCode()
    z = pcbnew.ZONE(b); z.SetLayer(pcbnew.In1_Cu); z.SetNetCode(code)
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for (x, y) in ((2, 2), (38, 2), (38, 38), (2, 38)): ch.Append(nm(x), nm(y))
    ch.SetClosed(True); z.Outline().AddOutline(ch)
    b.Add(z)
    fp = pcbnew.FOOTPRINT(b); fp.SetPosition(pcbnew.VECTOR2I(nm(20), nm(20)))
    pad = pcbnew.PAD(fp); pad.SetShape(pcbnew.PAD_SHAPE_RECT)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD); pad.SetDrillSize(pcbnew.VECTOR2I(0, 0))  # truly SMD: a default PAD carries a 0.762 PTH drill, which the corrected hole_to_hole check (#75) rightly rejects a via on top of
    pad.SetSize(pcbnew.VECTOR2I(nm(1.6), nm(1.6)))
    pad.SetPosition(pcbnew.VECTOR2I(nm(20), nm(20)))
    _ls = pcbnew.LSET(); _ls.AddLayer(pcbnew.F_Cu); pad.SetLayerSet(_ls); pad.SetNetCode(code)
    fp.Add(pad)
    b.Add(fp)
    # a second GND pad, already plane-connected through its own via:
    # bridging pad 1 into the plane must grow pad 1's pad set to 2.
    fp2 = pcbnew.FOOTPRINT(b); fp2.SetPosition(pcbnew.VECTOR2I(nm(30), nm(30)))
    pad2 = pcbnew.PAD(fp2); pad2.SetShape(pcbnew.PAD_SHAPE_RECT)
    pad2.SetAttribute(pcbnew.PAD_ATTRIB_SMD); pad2.SetDrillSize(pcbnew.VECTOR2I(0, 0))
    pad2.SetSize(pcbnew.VECTOR2I(nm(1.6), nm(1.6)))
    pad2.SetPosition(pcbnew.VECTOR2I(nm(30), nm(30)))
    pad2.SetLayerSet(_ls); pad2.SetNetCode(code)
    fp2.Add(pad2)
    b.Add(fp2)
    v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(nm(30), nm(30)))
    v.SetWidth(nm(0.6)); v.SetDrill(nm(0.3))
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.In1_Cu); v.SetNetCode(code)
    b.Add(v)
    w.refill(b)
    conn = b.GetConnectivity(); conn.Build(b)
    assert w._bridge_island(b, conn, 'GND', pad) is True

if __name__ == '__main__':
    test_pad_over_own_plane_not_filtered(); print('bridge filter OK')
