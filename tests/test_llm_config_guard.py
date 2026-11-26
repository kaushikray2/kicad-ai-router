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

"""#138 (B13) smaller item, main side: a non-numeric max_tool_calls
must not crash an LLM-mode job."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import orchestrator


class _DoneClient:
    def __init__(self, *a):
        pass

    def chat(self, messages, tools):
        return {'content': '', 'raw': {},
                'tool_calls': [{'name': 'done', 'args': {'note': 'ok'},
                                'id': '1'}]}


class _StubPipe:
    board_path = 'x.kicad_pcb'
    cfg = {}

    def finalize(self):
        return 'board'

    def tool_gate(self, board=None):
        return {}


def test_llm_mode_survives_non_numeric_max_tool_calls(monkeypatch):
    monkeypatch.setattr(orchestrator, 'LLMClient', _DoneClient)
    cfg = {'endpoint': 'http://unused', 'model': 'm',
           'max_tool_calls': 'not-a-number'}
    out = orchestrator.run_llm(cfg, _StubPipe(), lambda m: None)
    assert out['final_board'] == 'board'
    assert orchestrator._cfg_int({'max_tool_calls': '7'},
                                 'max_tool_calls', 48) == 7
    assert orchestrator._cfg_int({}, 'max_tool_calls', 48) == 48
