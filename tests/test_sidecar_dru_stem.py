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

"""Issue #198a: naming.sidecar_paths resolved the .kicad_dru
only against the winning stem -- when the single .kicad_pro in
the directory belonged to a different stem than the board, the
board's own <board-stem>.kicad_dru was never checked and the
renamed board silently lost its DRC rules (the #182 class, one
extension over). Run: python3 tests/test_sidecar_dru_stem.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import naming

def _touch(d, name):
    p = os.path.join(d, name)
    with open(p, 'w') as f: f.write('x')
    return p

def test_board_stem_dru_wins_over_project_stem():
    d = tempfile.mkdtemp()
    board = _touch(d, 'variant.kicad_pcb')
    _touch(d, 'variant.kicad_dru')
    _touch(d, 'myproj.kicad_pro')
    pro, dru = naming.sidecar_paths(board)
    assert pro == os.path.join(d, 'myproj.kicad_pro'), pro
    assert dru == os.path.join(d, 'variant.kicad_dru'), dru

def test_project_stem_dru_still_found():
    d = tempfile.mkdtemp()
    board = _touch(d, 'variant.kicad_pcb')
    _touch(d, 'myproj.kicad_pro')
    _touch(d, 'myproj.kicad_dru')
    pro, dru = naming.sidecar_paths(board)
    assert dru == os.path.join(d, 'myproj.kicad_dru'), dru

def test_same_stem_unchanged():
    d = tempfile.mkdtemp()
    board = _touch(d, 'demo.kicad_pcb')
    _touch(d, 'demo.kicad_pro'); _touch(d, 'demo.kicad_dru')
    pro, dru = naming.sidecar_paths(board)
    assert pro == os.path.join(d, 'demo.kicad_pro') and dru == os.path.join(d, 'demo.kicad_dru')

if __name__ == '__main__':
    test_board_stem_dru_wins_over_project_stem()
    test_project_stem_dru_still_found()
    test_same_stem_unchanged()
    print('sidecar dru stem OK')
