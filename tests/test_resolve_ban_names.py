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

"""N7/#49: the single layer-law resolution point (pipeline.resolve_ban_names). Pure python."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
from pipeline import resolve_ban_names

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}

def test_canonical():
    assert resolve_ban_names(['In1.Cu'], ALIASES) == (['In1.Cu'], [])

def test_board_name_and_case():
    assert resolve_ban_names(['GND'], ALIASES) == (['In1.Cu'], [])
    assert resolve_ban_names(['in1.cu'], ALIASES) == (['In1.Cu'], [])
    assert resolve_ban_names([' 3V3 '], ALIASES) == (['In2.Cu'], [])

def test_dedupe_and_unknown():
    canon, unknown = resolve_ban_names(['In1.Cu', 'GND', 'Nowhere.Cu'], ALIASES)
    assert canon == ['In1.Cu'] and unknown == ['Nowhere.Cu']
