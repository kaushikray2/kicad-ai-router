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

"""Phase 2E (#64): severance (barrier-closure) penalty in finisher
candidate scoring.

A candidate leg that closes a loop of placed copper, pads and
saturated channels around an unrouted connection's pads strands
that net; the finisher then pays multi-victim displacement rounds
to undo it. The worker's barrier model (union-find over placed-
copper components + the board edge; saturated gates weld groups
whose gap is too narrow to pass) prices that into _ilp_candidates:
50 per severed connection, 2 per crossed unrouted airwire, in A*
cost units.

Pinned here: the crossing/parity geometry (proper crossings only;
endpoint touches never count), the loop test (a gate welds only
when its gap is under the candidate net's pass width), the
on-loop exemption (a connection with a pad ON the loop is not
severed), the served connection's exclusion, and the board-level
model build (components, airwires of split nets only, gate gaps).
Requires pcbnew (KiCad python); skipped elsewhere.
Run: python3 tests/test_severance_scoring.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
try:
    import pcbnew
    import worker as w
except Exception:
    pcbnew = None

def nm(x): return int(round(x * 1e6))

def test_segs_cross_proper_only():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    assert w._segs_cross((0, 0), (10, 10), (0, 10), (10, 0)) is True
    assert w._segs_cross((0, 0), (10, 0), (0, 5), (10, 5)) is False   # parallel
    assert w._segs_cross((0, 0), (10, 0), (10, 0), (10, 10)) is False  # shared endpoint
    assert w._segs_cross((5, 0), (5, 10), (0, 0), (10, 0)) is False   # T touch
    assert w._segs_cross((0, 0), (4, 0), (6, -5), (6, 5)) is False    # disjoint

def test_seg_seg_closest_exact_cases():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    d, p, q = w._seg_seg_closest((0, 0, 10, 0), (5, -5, 5, 5))
    assert d == 0.0 and p == (5.0, 0.0) and q == (5.0, 0.0), (d, p, q)  # crossing
    d, p, q = w._seg_seg_closest((0, 0, 10, 0), (0, 3, 10, 3))
    assert abs(d - 3.0) < 1e-9, d                                       # parallels
    d, p, q = w._seg_seg_closest((0, 0, 4, 0), (6, 3, 6, 10))
    assert abs(d - (13 ** 0.5)) < 1e-9 and p == (4, 0) and q == (6, 3), (d, p, q)

def _fake_barrier():
    m = object.__new__(w._Barrier)
    m.pad_comp = {('N', 0.0, 5.0): 1, ('N', 10.0, 5.0): 2,
                  ('V', 5.0, 2.0): 3, ('V', 5.0, 8.0): 4,
                  ('W', 20.0, 2.0): 5, ('W', 20.0, 8.0): 6,
                  ('X', 0.0, 0.0): 1, ('X', 5.0, 8.0): 4}
    m.gates = {(1, 2): (0.1, (0.0, 0.0), (10.0, 0.0))}
    m.airwires = [
        {'net': 'N', 'p1': (0.0, 5.0), 'p2': (10.0, 5.0), 'c1': 1, 'c2': 2},   # the served connection
        {'net': 'V', 'p1': (5.0, 2.0), 'p2': (5.0, 8.0), 'c1': 3, 'c2': 4},    # inside -> outside: severed
        {'net': 'W', 'p1': (20.0, 2.0), 'p2': (20.0, 8.0), 'c1': 5, 'c2': 6},  # far away: untouched
        {'net': 'X', 'p1': (0.0, 0.0), 'p2': (5.0, 8.0), 'c1': 1, 'c2': 4},    # pad ON the loop: exempt
    ]
    return m

def test_severance_charge_loop_parity():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    m = _fake_barrier()
    A = ('U1.1', 0.0, 5.0, {0}); Bp = ('U2.1', 10.0, 5.0, {0})
    simp = [(0, 0.0, 5.0), (0, 10.0, 5.0)]
    charge, severed, xings = w._severance_charge(m, 'N', A, Bp, simp, 0.6)
    assert severed == ['V'], severed
    # crossings: V's airwire crosses the candidate path once; X's
    # crosses it once too (the crossing term counts it even though
    # the parity test exempts X); W never; the served N is excluded.
    assert xings == 2, xings
    assert charge == 50.0 * 1 + 2.0 * 2, charge

def test_severance_no_weld_no_severance():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    m = _fake_barrier()
    A = ('U1.1', 0.0, 5.0, {0}); Bp = ('U2.1', 10.0, 5.0, {0})
    simp = [(0, 0.0, 5.0), (0, 10.0, 5.0)]
    # Gate gap 0.1 >= need 0.05: the gate does not weld for this
    # net, the groups stay separate, no loop closes.
    charge, severed, xings = w._severance_charge(m, 'N', A, Bp, simp, 0.05)
    assert severed == [] and xings == 2 and charge == 4.0, (charge, severed, xings)

def _pad(b, ref, num, code, x, y):
    fp = pcbnew.FOOTPRINT(b); fp.SetReference(ref)
    fp.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
    p = pcbnew.PAD(fp); p.SetNumber(num)
    p.SetShape(pcbnew.PAD_SHAPE_RECT); p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetDrillSize(pcbnew.VECTOR2I(0, 0))
    p.SetSize(pcbnew.VECTOR2I(nm(1.0), nm(1.0)))
    p.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); p.SetLayerSet(ls)
    p.SetNetCode(code); fp.Add(p); b.Add(fp)
    return p

def _track(b, code, x1, y1, x2, y2):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); t.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
    t.SetWidth(nm(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(code); b.Add(t)
    return t

def test_barrier_model_on_synthetic_board():
    if pcbnew is None: print('SKIP: no pcbnew'); return
    b = pcbnew.BOARD(); b.SetCopperLayerCount(2)
    na = pcbnew.NETINFO_ITEM(b, 'NA'); b.Add(na); ca = na.GetNetCode()
    nb = pcbnew.NETINFO_ITEM(b, 'NB'); b.Add(nb); cb = nb.GetNetCode()
    for (x1, y1, x2, y2) in ((0, 0, 40, 0), (40, 0, 40, 30), (40, 30, 0, 30), (0, 30, 0, 0)):
        sh = pcbnew.PCB_SHAPE(b); sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
        sh.SetStart(pcbnew.VECTOR2I(nm(x1), nm(y1))); sh.SetEnd(pcbnew.VECTOR2I(nm(x2), nm(y2)))
        sh.SetLayer(pcbnew.Edge_Cuts); b.Add(sh)
    _pad(b, 'U1', '1', ca, 5, 5); _pad(b, 'U1', '2', ca, 35, 5)
    _pad(b, 'U2', '1', cb, 5, 25); _pad(b, 'U2', '2', cb, 35, 25)
    m = w._Barrier(b)
    assert len(m.pad_comp) == 4, m.pad_comp
    assert {x['net'] for x in m.airwires} == {'NA', 'NB'}, m.airwires
    assert all(g[0] <= w.SEVER_GATE_DMAX for g in m.gates.values())
    # Join NA's pads with copper: its airwire must disappear (the
    # net is no longer split), NB's must remain.
    _track(b, ca, 5, 5, 20, 5); _track(b, ca, 20, 5, 35, 5)
    m2 = w._Barrier(b)
    assert {x['net'] for x in m2.airwires} == {'NB'}, m2.airwires

if __name__ == '__main__':
    test_segs_cross_proper_only()
    test_seg_seg_closest_exact_cases()
    test_severance_charge_loop_parity()
    test_severance_no_weld_no_severance()
    test_barrier_model_on_synthetic_board()
    print('severance scoring OK')
