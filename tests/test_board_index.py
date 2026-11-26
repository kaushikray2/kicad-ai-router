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

"""board_index against the committed GUI fixture board (needs
pcbnew: runs standalone under system python3 and under pytest
where pcbnew is importable)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from ai_router import board_index

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'eng_gui_fixture', 'fixture.kicad_pcb')


def test_fixture_index():
    idx = board_index.load_index(FIXTURE)
    assert idx is not None
    assert idx['layers'] == ['F.Cu', 'B.Cu']
    for net in ('RF_IN', 'RF_FAIL', 'CLK_P', 'CLK_N', 'HV_A', 'HV_B',
                'LEN_A', 'LEN_B', 'QUIET_X', 'VCC5', 'GND'):
        assert net in idx['nets'], net
    u17 = [f for f in idx['footprints'] if f['ref'] == 'U17'][0]
    assert u17['pads'] == {'1': 'VCC5', '2': 'GND'}
    assert u17['properties'].get('AIRuleDecapMm') == '3.0'


def test_missing_board_is_none_not_exception():
    assert board_index.load_index('/nonexistent/board.kicad_pcb') is None
    assert board_index.load_index('') is None


if __name__ == '__main__':
    test_fixture_index()
    test_missing_board_is_none_not_exception()
    print('test_board_index: OK')
