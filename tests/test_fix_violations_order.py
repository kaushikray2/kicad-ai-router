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

"""#210 (a) regression: in run_auto, tool_fix_violations runs
BEFORE the engineered stages.  The fix rips a faulted net's
copper within +-3 mm and re-closes it at netclass width; run
after the engineered stages it could silently un-widen a power
rail or un-meander a length-matched run while the recorded plan
still claimed the sizing.  Drives _run_auto_once with a recording
stub pipe; the engineered stages are replaced by a recorder."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ai_router import orchestrator


class _StubPipe:
    def __init__(self, calls):
        self.calls = calls
        self.cfg = {}
        self.board_path = 'board.kicad_pcb'
        self.base = 'base.kicad_pcb'
        self.current = 'cur.kicad_pcb'
        self.last_gate = None
        self._audit = None
        self.workdir = None

    def _gate(self, *_a, **_k):
        return {'unconnected_total': 0, 'pour_items': 0, 'split_nets': [],
                'split_signal_nets': [], 'split_pour_nets': [],
                'violations_total': 0, 'board': self.current}

    def tool_gate(self, *_a, **_k):
        return self._gate()

    def tool_board_summary(self):
        return {'nets': [], 'copper_layers': [], 'pour_layers': {}}

    def tool_scorecard(self):
        return {}

    def tool_portfolio(self, _engines):
        return {}

    def tool_route(self, **_k):
        return {}

    def tool_fix_violations(self):
        self.calls.append('fix_violations')
        return {}

    def finalize(self):
        return 'final.kicad_pcb'

    def engineered_report(self, _final):
        return None

    def __getattr__(self, name):
        if name.startswith('tool_'):
            return lambda *_a, **_k: {}
        raise AttributeError(name)


def test_fix_violations_precedes_engineered_stages():
    calls = []
    pipe = _StubPipe(calls)
    orig = orchestrator._run_engineered_stages
    orchestrator._run_engineered_stages = lambda p, log: calls.append('engineered_stages')
    try:
        orchestrator.run_auto({'layer_law_policy': 'off'}, pipe, lambda m: None)
    finally:
        orchestrator._run_engineered_stages = orig
    assert 'fix_violations' in calls and 'engineered_stages' in calls, calls
    assert calls.index('fix_violations') < calls.index('engineered_stages'), calls
