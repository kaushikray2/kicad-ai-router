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

"""Issue #194: #185 forced deterministic off under mode='llm' at
the config boundaries (dialog, run_job.build_cfg), but a direct
Pipeline caller (CLI / bench / local driver) bypassed both and
still ran the engine with --deterministic in LLM mode. The
coercion belongs in Pipeline.__init__ too -- the layer every
construction path passes through. Run:
python3 tests/test_llm_pipeline_guard.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
from pipeline import Pipeline

def _pipe(cfg):
    d = tempfile.mkdtemp()
    board = os.path.join(d, 'b.kicad_pcb')
    with open(board, 'w') as f: f.write('(kicad_pcb)')
    logs = []
    base = {'strip_existing': False, 'route_open_only': False}
    base.update(cfg)
    return Pipeline(base, board, logs.append), logs

def test_llm_deterministic_coerced_off_in_pipeline():
    pipe, logs = _pipe({'mode': 'llm', 'deterministic': True})
    assert pipe.cfg.get('deterministic') is False, pipe.cfg.get('deterministic')
    assert any('deterministic forced off' in m for m in logs), logs

def test_auto_deterministic_untouched():
    pipe, logs = _pipe({'mode': 'auto', 'deterministic': True})
    assert pipe.cfg.get('deterministic') is True
    pipe2, _ = _pipe({'mode': 'llm'})
    assert not pipe2.cfg.get('deterministic')

if __name__ == '__main__':
    test_llm_deterministic_coerced_off_in_pipeline()
    test_auto_deterministic_untouched()
    print('llm pipeline guard OK')
