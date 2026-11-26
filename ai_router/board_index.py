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

"""Board index for the markings editor: the board's nets,
footprints (reference, value, pad -> net, AIRule* properties) and
copper layer names, read from the board FILE with pcbnew.

Guarded exactly like board_facts.load_facts: import-safe without
pcbnew, returns None on ANY failure, never raises -- the editor
then opens with an empty index and says so, rather than failing.
"""
import os

try:
    from . import board_facts
except ImportError:
    import board_facts

RULE_PROPERTIES = ('AIRuleDecapMm', 'AIRuleEdgeClearanceMm', 'AIRuleClass')


def load_index(board_path):
    # -> {'nets': [names], 'footprints': [{'ref', 'value',
    #     'pads': {pad: net}, 'properties': {name: text}}],
    #     'layers': [canonical copper names]}  or None.
    try:
        if not board_path or not os.path.exists(board_path):
            return None  # pcbnew.LoadBoard segfaults on a bad path
        import pcbnew
        b = pcbnew.LoadBoard(board_path)
        if b is None:
            return None
        canon, _ids = board_facts._stack(b, pcbnew)
        nets = set()
        footprints = []
        for fp in b.GetFootprints():
            pads = {}
            for pad in fp.Pads():
                net = pad.GetNetname()
                if net:
                    nets.add(net)
                pads[str(pad.GetNumber())] = net
            props = {}
            for name in RULE_PROPERTIES:
                try:
                    fld = fp.GetField(name)
                    if fld is not None and fld.GetText():
                        props[name] = fld.GetText()
                except Exception:
                    pass
            footprints.append({'ref': fp.GetReference(),
                               'value': fp.GetValue(),
                               'pads': pads, 'properties': props})
        for t in b.GetTracks():
            if t.GetNetname():
                nets.add(t.GetNetname())
        for z in b.Zones():
            if z.GetNetname():
                nets.add(z.GetNetname())
        footprints.sort(key=lambda f: f['ref'])
        return {'nets': sorted(nets), 'footprints': footprints,
                'layers': list(canon)}
    except Exception:
        return None
