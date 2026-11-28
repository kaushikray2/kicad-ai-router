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

"""Issues #234 + #235: the meander's board-outline test and
its same-net obstacle handling.

#234: cmd_meander chains Edge.Cuts segments into outline_loop
behind `while grew and len(loop) > 2` -- but every chain
starts with exactly 2 points, so the loop never ran,
outline_loop was always None, and the site test never checked
the board outline at all: a meander could be drawn off the
board. #235: site_ok treated the candidate net's OWN pads and
vias as obstacles (need = w/2 + r from the pad centre), so on
a short pad-to-pad segment -- whose endpoint pads are always
same-net and whose lobe legs start as little as 0.075*L from
the pad centre -- both sides were rejected in empty space.
Run: python3 tests/test_meander_outline.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import worker


def _require_pcbnew():
    try:
        import pcbnew  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import pytest
        pytest.skip('pcbnew unavailable')
    except ImportError:
        print('skip: pcbnew unavailable')
    return False


def _nm(x):
    return int(round(x * 1e6))


def _board(path, seg_span, outline=None, rect_outline=False):
    import pcbnew
    V = pcbnew.VECTOR2I
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    n = pcbnew.NETINFO_ITEM(b, 'SIG')
    b.Add(n)
    x1, x2, y = seg_span
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(V(_nm(x1), _nm(y))); t.SetEnd(V(_nm(x2), _nm(y)))
    t.SetWidth(_nm(0.3)); t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(n.GetNetCode())
    b.Add(t)
    for ref, x in (('U1', x1), ('U2', x2)):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        p = pcbnew.PAD(f)
        p.SetNumber('1'); p.SetPosition(V(_nm(x), _nm(y)))
        p.SetSize(V(_nm(1.0), _nm(1.0)))
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNetCode(n.GetNetCode())
        f.Add(p)
        b.Add(f)
    if outline:
        if rect_outline:
            (rx0, ry0), (rx1, ry1) = outline[0], outline[2]
            s = pcbnew.PCB_SHAPE(b)
            s.SetShape(pcbnew.SHAPE_T_RECT)
            s.SetStart(V(_nm(rx0), _nm(ry0))); s.SetEnd(V(_nm(rx1), _nm(ry1)))
            s.SetLayer(pcbnew.Edge_Cuts)
            b.Add(s)
        else:
            for i in range(len(outline)):
                ax, ay = outline[i]; bx, by = outline[(i + 1) % len(outline)]
                s = pcbnew.PCB_SHAPE(b)
                s.SetShape(pcbnew.SHAPE_T_SEGMENT)
                s.SetStart(V(_nm(ax), _nm(ay))); s.SetEnd(V(_nm(bx), _nm(by)))
                s.SetLayer(pcbnew.Edge_Cuts)
                b.Add(s)
    pcbnew.SaveBoard(path, b)
    return path


def _drawn_ys(path):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    ys = []
    for t in b.GetTracks():
        if t.GetNetname() == 'SIG':
            ys += [t.GetStart().y / 1e6, t.GetEnd().y / 1e6]
    return ys


def test_outline_blocks_off_board_side():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        # outline +side edge at y=21, segment at y=20: the +
        # side's lobes (h up to 3 mm) would leave the board.
        path = _board(os.path.join(td, 'e.kicad_pcb'), (10, 90, 20),
                      outline=[(2, 2), (98, 2), (98, 21), (2, 21)])
        res = worker.cmd_meander(path, 'SIG', 6.0)
        assert res['added_mm'] > 5.9, res
        assert res['side'] == -1, res   # + side is off-board
        ys = _drawn_ys(path)
        assert max(ys) <= 21.0 + 1e-6, ys


def test_outline_from_rect_shape():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        # the same outline drawn as ONE rect shape (the common
        # KiCad way) must chain too, via tessellation.
        path = _board(os.path.join(td, 'r.kicad_pcb'), (10, 90, 20),
                      outline=[(2, 2), (98, 2), (98, 21), (2, 21)],
                      rect_outline=True)
        res = worker.cmd_meander(path, 'SIG', 6.0)
        assert res['added_mm'] > 5.9, res
        assert res['side'] == -1, res
        ys = _drawn_ys(path)
        assert max(ys) <= 21.0 + 1e-6, ys


def test_same_net_endpoint_pads_are_not_obstacles():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        # 6.2 mm pad-to-pad segment, nothing else on the board:
        # the only 'obstructions' are the segment's own pads.
        path = _board(os.path.join(td, 's.kicad_pcb'), (10, 16.2, 20))
        res = worker.cmd_meander(path, 'SIG', 2.0)
        assert res['added_mm'] > 1.9, res
        assert abs(res['added_mm'] - 2.0) < 0.05, res


if __name__ == '__main__':
    test_outline_blocks_off_board_side()
    test_outline_from_rect_shape()
    test_same_net_endpoint_pads_are_not_obstacles()
    print('meander outline OK')
