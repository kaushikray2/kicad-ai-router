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

"""v1.11.2/#103: travel on a layer the caller vouches for via
extra_layers (a net's OWN plane layer at every call site) must cost
the unit rate, not the layer_cost factor. Before the fix, relaxing a
hard ban into a price (In1.Cu=10) made a net's own-plane heal bridge
10x per cell -- dearer than under the ban it replaced, so past
~0.56 mm the planner preferred 2 vias to an outer layer over its
own plane.
Run: python3 tests/test_extra_layer_pricing.py  (needs pcbnew)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))

def test_extra_layer_travels_at_unit_cost():
    try:
        import pcbnew
        import worker as w
    except Exception:
        print('SKIP extra-layer pricing test: no pcbnew'); return
    b = pcbnew.BOARD(); b.SetCopperLayerCount(4)
    saved = (w.BANNED_LAYER_IDS, dict(w.LAYER_COST_BY_ID), list(w.LAYER_COST_UNRESOLVED), list(w.NO_TRACKS_ON_UNRESOLVED))
    try:
        w.BANNED_LAYER_IDS = set()                      # nothing banned
        w.LAYER_COST_BY_ID = {pcbnew.In2_Cu: 10}        # stack idx 2 priced 10x
        w.LAYER_COST_UNRESOLVED = []; w.NO_TRACKS_ON_UNRESOLVED = []
        g = w.Grid(b, (0.0, 10.0, 0.0, 10.0))
        assert g._lfac == [1.0, 1.0, 10.0, 1.0], g._lfac
        blocked, via_block = g.build(set())
        # vouched (own-plane): stay on the priced layer at unit cost
        simp, cost = g.astar(blocked, via_block, (1.0, 1.0), (9.0, 1.0), (2,), (2,), extra_layers={2})
        assert simp and all(l == 2 for (l, _x, _y) in simp), simp
        assert 70.0 < cost < 100.0, cost  # 80 cells at 1.0; pre-fix: 130 via-detour or 800 direct
        # unvouched: pricing still applies -- the search detours to a
        # free layer (25 + 80 + 25 beats 800), exactly as before
        simp2, cost2 = g.astar(blocked, via_block, (1.0, 1.0), (9.0, 1.0), (2,), (2,))
        assert simp2 and any(l == 0 for (l, _x, _y) in simp2), simp2
        assert cost2 > 100.0, cost2
        # cutset_astar shares the rule
        simp3, _victims = g.cutset_astar(blocked, via_block, {}, {}, (1.0, 1.0), (9.0, 1.0), (2,), (2,), {}, nets=[], extra_layers={2})
        assert simp3 and all(l == 2 for (l, _x, _y) in simp3), simp3
    finally:
        w.BANNED_LAYER_IDS, w.LAYER_COST_BY_ID, w.LAYER_COST_UNRESOLVED, w.NO_TRACKS_ON_UNRESOLVED = saved

if __name__ == '__main__':
    test_extra_layer_travels_at_unit_cost()
    print('extra layer pricing OK')
