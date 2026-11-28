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

"""Phase 2E item 4 (#64/#65): the config keys exist and default OFF.

The bench harness warns on unknown cfg override keys (#225a) but a
renamed key would still silently change nothing in the pipeline;
these defaults are what the A/B arms override, so pin them: both
mechanisms ship dark until their verdicts say otherwise.
Run: python3 tests/test_item4_flags.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import config as cfgmod

def test_item4_flags_default_off():
    assert cfgmod.DEFAULTS['severance_scoring'] is False
    assert cfgmod.DEFAULTS['regret_order'] is False

if __name__ == '__main__':
    test_item4_flags_default_off()
    print('item4 flags OK')
