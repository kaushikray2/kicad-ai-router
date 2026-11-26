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

"""Issue #176: Reproducible run (deterministic) had three holes:

- the portfolio still ran freerouting (-mt 2, run-variant) and krt
  (no determinism contract) when a user configured them;
- the dialog allowed Reproducible + LLM mode together, under a
  caption saying the LLM path varies run to run;
- the engine-leg subprocess timeouts (3900 / 2400 / 1900 s) were
  fixed wall-clock values tuned at the 20M work budget, so a
  recalibrated budget lets a healthy leg outgrow its timeout.

Run: python3 tests/test_deterministic_hygiene.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_router'))
import pipeline as pl
import engine_run

ALIASES = {'f.cu': 'F.Cu', 'b.cu': 'B.Cu', 'in1.cu': 'In1.Cu', 'gnd': 'In1.Cu',
           'in2.cu': 'In2.Cu', '3v3': 'In2.Cu'}
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
GATE = {'unconnected_total': 0, 'copper_violations': 0,
        'banned_layer_tracks': 0, 'split_nets': [], 'classes': {}}


def _fake_pipe(cfg):
    d = tempfile.mkdtemp()
    pipe = object.__new__(pl.Pipeline)
    pipe.workdir = d; pipe.pass_no = 0; pipe._oseq = 0; pipe.ledger = []
    pipe.base = os.path.join(d, 'base.kicad_pcb'); pipe.current = pipe.base
    pipe.cfg = cfg; pipe.last_gate = dict(GATE); pipe.last_route = None
    pipe.logs = []; pipe.log = lambda m: pipe.logs.append(m)
    pipe._copper_layers = list(LAYERS)
    pipe.calls = []
    def fake_worker(cmd, *args, **kw):
        pipe.calls.append(cmd)
        if cmd == 'summary':
            return {'copper_layers': list(LAYERS), 'layer_aliases': dict(ALIASES)}
        if '--out' in args:
            open(args[args.index('--out') + 1], 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._worker = fake_worker
    def fake_variant(eng, outp):
        pipe.calls.append('variant:' + eng)
        open(outp, 'w').write('board')
        return {'status': 'ok', 'seconds': 0.1}
    pipe._route_variant = fake_variant
    pipe.tool_gate = lambda board: dict(GATE)
    return pipe


def test_portfolio_excludes_scratch_engines_when_deterministic():
    pipe = _fake_pipe({'tracemaker_path': '/tm/tracemaker', 'deterministic': True})
    pipe.tool_portfolio(['freerouting', 'krt', 'tm_relax'])
    assert 'freeroute' not in pipe.calls, pipe.calls
    assert 'krt_route' not in pipe.calls, pipe.calls
    assert pipe.calls.count('variant:tm_relax') == 1, pipe.calls
    rec = [e for e in pipe.ledger if e['step'] == 'portfolio'][0]['data']
    assert rec['engines'] == ['tm_relax'], rec
    assert rec.get('excluded_deterministic') == ['freerouting', 'krt'], rec


def test_scaled_timeout():
    st = engine_run.scaled_timeout
    # unchanged at and below the budget the constants were tuned at
    assert st(3900, 20000000) == 3900
    assert st(2400, 10000000) == 2400
    assert st(1900, 20000000) == 1900
    # linear above it: the recalibrated 40M budget doubles the legs
    assert st(3900, 40000000) == 7800
    assert st(2400, 40000000) == 4800
    assert st(1900, 40000000) == 3800
    assert st(1900, 60000000) == 5700
    # junk budgets fall back to the tuned values, never below them
    assert st(3900, None) == 3900
    assert st(3900, 'x') == 3900
    assert st(3900, 0) == 3900


class _Ctl:
    def __init__(self, value=False):
        self._v = value; self.enabled = True; self.tip = None
    def GetValue(self): return self._v
    def Enable(self, on=True): self.enabled = bool(on)
    def SetToolTip(self, tip): self.tip = tip


def _dialog_part():
    try:
        import wx  # noqa: F401
    except ImportError:
        import pytest
        pytest.importorskip('wx')
        return
    import importlib.machinery, importlib.util
    ld = importlib.machinery.SourceFileLoader('dlg_uut', os.path.join(os.path.dirname(__file__), '..', 'ai_router', 'dialog.py'))
    sp = importlib.util.spec_from_loader('dlg_uut', ld); dlg = importlib.util.module_from_spec(sp); sp.loader.exec_module(dlg)
    d = dlg.RouterDialog.__new__(dlg.RouterDialog)  # wx classes refuse object.__new__; no __init__, controls are stubs
    d._running = False
    d._det_tip = 'AUTO-TIP'
    d.mode_llm_rb = _Ctl(True)
    d.det_ctrl = _Ctl(True)
    d._apply_det_state()
    assert d.det_ctrl.enabled is False, 'LLM mode must disable Reproducible'
    assert d.det_ctrl.tip and 'Auto mode' in d.det_ctrl.tip, d.det_ctrl.tip
    d.mode_llm_rb = _Ctl(False)
    d._apply_det_state()
    assert d.det_ctrl.enabled is True, 'Auto mode must re-enable Reproducible'
    assert d.det_ctrl.tip == 'AUTO-TIP', d.det_ctrl.tip
    # during a run _set_running has disabled every control; the
    # state applier must not re-enable Reproducible behind its back
    d._running = True
    d.det_ctrl.enabled = False
    d._apply_det_state()
    assert d.det_ctrl.enabled is False


def test_dialog_det_state():
    _dialog_part()


if __name__ == '__main__':
    test_portfolio_excludes_scratch_engines_when_deterministic()
    test_scaled_timeout()
    _dialog_part()
    print('deterministic hygiene OK')
