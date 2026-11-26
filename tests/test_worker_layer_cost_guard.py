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

"""v1.11.2/#105: the worker's --layer-cost parse must apply the
same guards as the pipeline's parse_layer_cost -- non-empty name,
digits-only factor, 1..1000 inclusive. Before the fix it accepted
anything int() would parse: 0 made travel on a priced layer free,
negatives made A* step costs negative, >1000 exceeded the engine's
own maximum -- whenever a spec reached the worker without passing
through the pipeline (direct runs, forwarded child invocations).
A guarded-out token prices nothing (the worker's salvage rule);
unlike the config side, valid sibling tokens still apply.
Run: python3 tests/test_worker_layer_cost_guard.py  (needs pcbnew)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))

def test_worker_layer_cost_guards():
    try:
        import pcbnew
        import worker as w
    except Exception:
        print('SKIP worker layer-cost guard test: no pcbnew'); return
    P = w._parse_layer_cost_raw
    assert P('In1.Cu=10,In2.Cu=10') == ({pcbnew.In1_Cu: 10, pcbnew.In2_Cu: 10}, [])
    assert P('In1.Cu=1') == ({pcbnew.In1_Cu: 1}, [])        # bounds are inclusive
    assert P('In1.Cu=10.5') == ({pcbnew.In1_Cu: 10.5}, [])  # v1.11.5 (#121): fractional factors reach Grid._lfac as floats
    assert P('In1.Cu=1000') == ({pcbnew.In1_Cu: 1000}, [])
    assert P('In1.Cu= 10 ') == ({pcbnew.In1_Cu: 10}, [])    # whitespace tolerated
    for bad in ('In1.Cu=0', 'In1.Cu=1001', 'In1.Cu=-3', 'In1.Cu=+10', 'In1.Cu=10.555',
                'In1.Cu=x', 'In1.Cu=', 'In1.Cu', '=10'):
        assert P(bad) == ({}, []), bad
    assert P('GND=5') == ({}, [('GND', 5)])                # board-alias names stay unresolved for Grid
    assert P('In1.Cu=10,In2.Cu=0') == ({pcbnew.In1_Cu: 10}, [])  # bad token prices nothing, good token survives

if __name__ == '__main__':
    test_worker_layer_cost_guards()
    print('worker layer cost guard OK')
