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

"""Board facts for the dialog (v1.12, #168): the open board's
stackup and zone map, read from the board FILE with pcbnew -- the
same data the worker's board_summary computes at run start
(copper_layers / layer_aliases / pour_layers), surfaced at dialog
open time instead.

Import-safe without pcbnew, and load_facts returns None on ANY
failure: detection is an aid, never a gate (proposal 4.4 fallback --
the dialog then keeps the saved config exactly as before). Never
raises.
"""
import os


def _stack(b, pcbnew):
    # Canonical copper names + pcbnew layer IDs, top to bottom --
    # the worker.copper_stack construction (KiCad numbers copper
    # out of stack order, so positions come from this table).
    n = b.GetCopperLayerCount()
    if n <= 1:
        return ['F.Cu'], [pcbnew.F_Cu]
    if n == 2:
        return ['F.Cu', 'B.Cu'], [pcbnew.F_Cu, pcbnew.B_Cu]
    canon = ['F.Cu'] + ['In%d.Cu' % k for k in range(1, n - 1)] + ['B.Cu']
    ids = [pcbnew.F_Cu] + [getattr(pcbnew, 'In%d_Cu' % k) for k in range(1, n - 1)] + [pcbnew.B_Cu]
    return canon, ids


def load_facts(board_path):
    # -> {'layers': [{'canonical':..., 'name':...}, ...],
    #     'zones_by_layer': {canonical: [net, ...]},
    #     'copper_count': int, 'zone_count': int}  or None.
    try:
        if not board_path or not os.path.exists(board_path):
            return None  # pcbnew.LoadBoard segfaults on a bad path
        import pcbnew
        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return None
        canon, ids = _stack(b, pcbnew)
        layers, id2canon = [], {}
        for lid, c in zip(ids, canon):
            layers.append({'canonical': c, 'name': b.GetLayerName(lid)})
            id2canon[lid] = c
        zones_by_layer = {c: [] for c in canon}
        nzones = 0
        for z in b.Zones():
            try:
                if z.GetIsRuleArea():
                    continue  # (#181): rule areas are not pours -- counting them inflated the census (Nomad fixture: 19 reported, 8 real zones)
            except Exception:
                pass
            nzones += 1
            net = z.GetNetname()
            if not net:
                continue
            try:
                lset = list(z.GetLayerSet().CuStack())
            except Exception:
                lset = [z.GetLayer()]
            for lid in lset:
                c = id2canon.get(lid)
                if c:
                    zones_by_layer[c].append(net)
        return {'layers': layers, 'zones_by_layer': zones_by_layer,
                'copper_count': len(canon), 'zone_count': nzones}
    except Exception:
        return None
