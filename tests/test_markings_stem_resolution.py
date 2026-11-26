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

"""#204 regression: the markings family resolves project sidecar
files (.ai-router-markings.json / .net / .kicad_sch) through the
#182 project-stem logic in naming.project_sidecar_path, so a
generated result board (<project>-YYYYMMDD-HHMMSS.kicad_pcb)
finds its project's files."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import backend_client, markings, markings_model, naming


def _project(tmp_path):
    d = str(tmp_path)
    (tmp_path / 'demo.kicad_pro').write_text('{}')
    (tmp_path / 'demo.ai-router-markings.json').write_text(
        json.dumps({'version': 1, 'impedance': []}))
    (tmp_path / 'demo.net').write_text('(netlist)')
    (tmp_path / 'demo.kicad_sch').write_text('(schematic)')
    board = os.path.join(d, 'demo-20261126-120000.kicad_pcb')
    (tmp_path / 'demo-20261126-120000.kicad_pcb').write_text('(board)')
    return d, board


def test_result_board_finds_project_sidecar(tmp_path):
    _d, board = _project(tmp_path)
    assert markings.markings_path_for(board).endswith(
        'demo.ai-router-markings.json')
    loaded = markings.load_markings(board)
    assert loaded is not None and loaded.data['version'] == 1


def test_result_board_finds_project_netlist(tmp_path):
    _d, board = _project(tmp_path)
    assert backend_client._netlist_bytes(board) == b'(netlist)'
    cond = markings_model.evaluate_conditions(board, {})
    assert cond['netlist'] is True and cond['schematic'] is True


def test_own_stem_wins_and_plain_board_unchanged(tmp_path):
    d, board = _project(tmp_path)
    own = os.path.join(d, 'demo-20261126-120000.net')
    (tmp_path / 'demo-20261126-120000.net').write_text('(own)')
    assert naming.project_sidecar_path(board, '.net') == own
    plain = os.path.join(d, 'demo.kicad_pcb')
    assert markings.markings_path_for(plain).endswith(
        'demo.ai-router-markings.json')
    assert naming.project_sidecar_path(plain, '.net').endswith('demo.net')
