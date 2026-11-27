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

"""#69 meander SI upgrade: 45-degree chamfered trapezoid lobes
with closed-form length compensation, amplitude capped at
3.0 mm, leg-to-leg spacing floor S >= max(3W, 3H_diel,
2*clr + 0.2), both-sides candidate search with an in-worker
obstacle test.  Pre-upgrade the worker drew ONE-SIDED
rectangular bumps up to 6 mm tall with no spacing floor and no
obstacle test at all."""
import math, os, sys, tempfile
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


def test_plan_math():
    SQ2 = math.sqrt(2.0)
    plan = worker._meander_plan(10.0, 34.0, 0.3, 0.9)
    assert plan is not None
    n, h, pitch, d_top, footprint = plan
    assert h <= 3.0
    # closed form: the drawn add is exact, not approximate
    assert abs(2.0 * n * h * (SQ2 - 1.0) - 10.0) < 1e-9
    assert footprint <= 34.0
    # a tall single loop is never used: 30 mm of add takes
    # several lobes, each within the cap
    n2, h2 = worker._meander_plan(30.0, 200.0, 0.3, 0.9)[:2]
    assert n2 >= 2 and h2 <= 3.0
    # a confined span at a large add is infeasible, not drawn
    assert worker._meander_plan(34.5, 12.75, 0.3, 0.9) is None


def _nm(x):
    return int(round(x * 1e6))


def _board(path, with_pour=False, blocker=False):
    import pcbnew
    V = pcbnew.VECTOR2I
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    nets = {}
    for name in ('SIG', 'GND', 'BLK'):
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n

    def seg(net, x1, y1, x2, y2, w=0.3):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(_nm(x1), _nm(y1))); t.SetEnd(V(_nm(x2), _nm(y2)))
        t.SetWidth(_nm(w)); t.SetLayer(pcbnew.F_Cu)
        t.SetNetCode(nets[net].GetNetCode())
        b.Add(t)

    def pad(ref, num, net, x, y):
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(ref)
        p = pcbnew.PAD(f)
        p.SetNumber(num); p.SetPosition(V(_nm(x), _nm(y)))
        p.SetSize(V(_nm(1.0), _nm(1.0)))
        p.SetShape(pcbnew.PAD_SHAPE_RECT)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetNetCode(nets[net].GetNetCode())
        f.Add(p)
        b.Add(f)
    seg('SIG', 10, 20, 90, 20)
    pad('U1', '1', 'SIG', 10, 20)
    pad('U2', '1', 'SIG', 90, 20)
    if blocker:
        seg('BLK', 5, 22.6, 95, 22.6)
        pad('U3', '1', 'BLK', 5, 22.6)
        pad('U4', '1', 'BLK', 95, 22.6)
    if with_pour:
        z = pcbnew.ZONE(b)
        ls = pcbnew.LSET(); ls.AddLayer(pcbnew.B_Cu)
        z.SetLayerSet(ls)
        z.SetNetCode(nets['GND'].GetNetCode())
        out = z.Outline(); out.NewOutline()
        chain = out.Outline(0)
        for (x, y) in [(2, 2), (98, 2), (98, 38), (2, 38)]:
            chain.Append(_nm(x), _nm(y))
        b.Add(z)
    pcbnew.SaveBoard(path, b)
    if with_pour:
        src = open(path).read()
        stackup = ('(stackup (layer "F.Cu" (type "copper") (thickness 0.035)) '
                   '(layer "dielectric 1" (type "core") (thickness 1.0) '
                   '(material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)) '
                   '(layer "B.Cu" (type "copper") (thickness 0.035)))')
        i = src.find('(setup')
        j = src.find('(stackup', i)
        if j < 0:
            src = src[:i + 6] + ' ' + stackup + src[i + 6:]
        else:
            k = src.find('(layer "F.Cu"', j)
            src = src[:j] + stackup + src[src.find(')', src.rfind('(layer', j, k)) if False else j:]
            # simpler: replace wholesale below if this path taken
        open(path, 'w').write(src)
    return path


def _drawn(path, net='SIG'):
    import pcbnew
    b = pcbnew.LoadBoard(path)
    segs = []
    for t in b.GetTracks():
        if t.GetNetname() == net and t.Type() == pcbnew.PCB_TRACE_T:
            segs.append(((t.GetStart().x / 1e6, t.GetStart().y / 1e6),
                         (t.GetEnd().x / 1e6, t.GetEnd().y / 1e6)))
    return segs


def _min_parallel_leg_gap(segs):
    best = None
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, bq = segs[i]
            c, d = segs[j]
            if a in (c, d) or bq in (c, d):
                continue          # shares an endpoint: joined copper
            ux1, uy1 = bq[0] - a[0], bq[1] - a[1]
            ux2, uy2 = d[0] - c[0], d[1] - c[1]
            l1 = math.hypot(ux1, uy1) or 1.0
            l2 = math.hypot(ux2, uy2) or 1.0
            if abs((ux1 * ux2 + uy1 * uy2) / (l1 * l2)) < 0.9:
                continue          # not a parallel pair
            if abs(uy1) < 1e-9 and abs(uy2) < 1e-9:
                continue          # collinear baseline pieces
            dist = worker._mx_seg_seg(a, bq, c, d)
            if best is None or dist < best:
                best = dist
    return best


def test_spacing_floor_and_compensation():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        path = _board(os.path.join(td, 'm.kicad_pcb'))
        res = worker.cmd_meander(path, 'SIG', 10.0)
        assert res['added_mm'] > 9.9, res     # chamfer-compensated
        assert abs(res['added_mm'] - 10.0) < 0.05, res
        assert res['bump_h_mm'] <= 3.0, res
        assert res['spacing_mm'] == 0.9, res  # max(3W, 0, 2clr+.2)
        segs = _drawn(path)
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs)
        assert abs(total - 90.0) < 0.05, total
        gap = _min_parallel_leg_gap(segs)
        # centreline distance >= S + W (edge gap >= S)
        assert gap is not None and gap >= 0.9 + 0.3 - 0.01, gap
        assert max(max(a[1], b[1]) for a, b in segs) <= 20.0 + 3.0 + 1e-6


def test_both_sides_fallback():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        path = _board(os.path.join(td, 'm2.kicad_pcb'), blocker=True)
        res = worker.cmd_meander(path, 'SIG', 6.0)
        assert res['added_mm'] > 5.9, res
        assert res['side'] == -1, res   # + side is blocked by BLK


def test_h_diel_from_pour_stackup():
    if not _require_pcbnew():
        return
    with tempfile.TemporaryDirectory() as td:
        path = _board(os.path.join(td, 'm3.kicad_pcb'), with_pour=True)
        res = worker.cmd_meander(path, 'SIG', 6.0)
        assert res['added_mm'] > 5.9, res
        assert res['h_diel_mm'] == 1.0, res
        assert res['spacing_mm'] == 3.0, res   # 3H governs
        gap = _min_parallel_leg_gap(_drawn(path))
        assert gap is not None and gap >= 3.0 + 0.3 - 0.01, gap


if __name__ == '__main__':
    test_plan_math()
    test_spacing_floor_and_compensation()
    test_both_sides_fallback()
    test_h_diel_from_pour_stackup()
    print('meander SI OK')
