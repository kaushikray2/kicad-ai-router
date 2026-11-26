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

"""Issue #191: since #189 the Pipeline works on its own
construction-time copy of cfg, but run_auto switched Variant B on
by mutating the CALLER's dict and reusing the already-constructed
pipe -- so 'Variant B' ran Variant A's layer law while the log
claimed In2.Cu was banned (proven on the v1.12.3 Nomad acceptance
job: the B run's ledger records no_tracks_on_effective [In1.Cu]).
Variant B must run on its OWN Pipeline built from an explicit cfg
copy carrying the In2 ban -- the construction Variant A already
used. Run: python3 tests/test_variant_b_pipeline_cfg.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import orchestrator as orc

class FakePipe:
    def __init__(self, cfg, board_path, log):
        self.cfg = dict(cfg); self.board_path = board_path
    def tool_board_summary(self):
        return {'copper_layers': ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'],
                'pour_layers': {'GND': ['In2.Cu']}, 'layer_aliases': {}}

def _drive(b_gate):
    calls = []
    orig_pipeline, orig_once = orc.Pipeline, orc._run_auto_once
    def factory(cfg, board_path, log):
        return FakePipe(cfg, board_path, log)
    def once(cfg, pipe, log):
        calls.append((cfg, pipe))
        gate = b_gate if len(calls) == 1 else {'split_signal_nets': [], 'split_pour_nets': [], 'unconnected_total': 0}
        return {'gate': gate, 'final_board': None}
    orc.Pipeline, orc._run_auto_once = factory, once
    try:
        passed = FakePipe({'no_tracks_on': 'In1.Cu'}, '/x/board.kicad_pcb', lambda m: None)
        cfg = {'no_tracks_on': 'In1.Cu', 'layer_law_policy': 'auto'}
        orc.run_auto(cfg, passed, lambda m: None)
        return calls, passed, cfg
    finally:
        orc.Pipeline, orc._run_auto_once = orig_pipeline, orig_once

def test_variant_b_runs_on_its_own_pipeline_with_in2_banned():
    calls, passed, cfg = _drive({'split_signal_nets': [], 'split_pour_nets': []})
    assert len(calls) == 1, calls
    bcfg, bpipe = calls[0]
    assert bpipe is not passed, 'Variant B reused the caller-constructed pipeline'
    assert bpipe.cfg.get('no_tracks_on') == 'In1.Cu,In2.Cu', bpipe.cfg
    assert bcfg.get('no_tracks_on') == 'In1.Cu,In2.Cu'
    assert cfg.get('no_tracks_on') == 'In1.Cu'
    assert passed.cfg.get('no_tracks_on') == 'In1.Cu'

def test_variant_b_escalation_still_builds_a_with_in1_only():
    calls, passed, cfg = _drive({'split_signal_nets': [], 'split_pour_nets': ['3V3'], 'unconnected_total': 3})
    assert len(calls) == 2, calls
    assert calls[0][1].cfg.get('no_tracks_on') == 'In1.Cu,In2.Cu', calls[0][1].cfg
    assert calls[1][1].cfg.get('no_tracks_on') == 'In1.Cu', calls[1][1].cfg
    assert calls[1][1] is not calls[0][1]

if __name__ == '__main__':
    test_variant_b_runs_on_its_own_pipeline_with_in2_banned()
    test_variant_b_escalation_still_builds_a_with_in1_only()
    print('variant B pipeline cfg OK')
